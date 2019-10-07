#!/usr/bin/env python
"""Signingscript task functions."""
import asyncio
import base64
import difflib
import fnmatch
import glob
import json
import logging
import os
import re

# TODO: Use aiohttp for this.
import requests
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from requests_hawk import HawkAuth
from mardor.reader import MarReader
from mardor.writer import add_signature_block

from scriptworker.utils import (
    get_single_item_from_sequence,
    makedirs,
    raise_future_exceptions,
    retry_async,
    rm,
)

from signingscript import task
from signingscript import utils
from signingscript.createprecomplete import generate_precomplete
from signingscript.exceptions import SigningScriptError

try:
    # NB. The widevine module needs to be deployed separately
    import widevine
except ImportError:
    widevine = None

import winsign.sign
from winsign.crypto import load_pem_certs

sys.path.append(  # append the mozbuild vendor
    os.path.abspath(
        os.path.join(
            os.path.realpath(os.path.dirname(__file__)), "vendored", "mozbuild"
        )
    )
)

from mozpack import mozjar  # noqa: E402


log = logging.getLogger(__name__)

_ZIP_ALIGNMENT = (
    "4"
)  # Value must always be 4, based on https://developer.android.com/studio/command-line/zipalign.html

# Blessed files call the other widevine files.
_WIDEVINE_BLESSED_FILENAMES = (
    # plugin-container is the top of the calling stack
    "plugin-container",
    "plugin-container.exe",
)
# These are other files that need to be widevine-signed
_WIDEVINE_NONBLESSED_FILENAMES = (
    # firefox
    "firefox",
    "firefox-bin",
    "firefox.exe",
    # xul
    "libxul.so",
    "XUL",
    "xul.dll",
    # clearkey for regression testing.
    "clearkey.dll",
    "libclearkey.dylib",
    "libclearkey.so",
)

# These are the keys used to verify if a keyid isn't specified
_DEFAULT_MAR_VERIFY_KEYS = {
    "autograph_stage_mar384": {"dep-signing": "autograph_stage.pem"},
    "autograph_hash_only_mar384": {
        "release-signing": "release_primary.pem",
        "nightly-signing": "nightly_aurora_level3_primary.pem",
        "dep-signing": "dep1.pem",
    },
}

# Langpacks expect the following re to match for addon id
LANGPACK_RE = re.compile(
    r"^langpack-[a-zA-Z]+(?:-[a-zA-Z]+){0,2}@(?:firefox|devedition).mozilla.org$"
)


# get_suitable_signing_servers {{{1
def get_suitable_signing_servers(
    signing_servers, cert_type, signing_formats, raise_on_empty_list=False
):
    """Get the list of signing servers for given `signing_formats` and `cert_type`.

    Args:
        signing_servers (dict of lists of lists): the contents of
            `signing_server_config`.
        cert_type (str): the certificate type - essentially signing level,
            separating release vs nightly vs dep.
        signing_formats (list): the signing formats the server needs to support
        raise_on_empty_list (bool): flag to raise errors. Optional. Defaults to False.

    Raises:
        FailedSubprocess: on subprocess error while signing.
        SigningScriptError: when no suitable signing server is found

    Returns:
        list of lists: the list of signing servers.

    """
    if cert_type not in signing_servers:
        suitable_signing_servers = []
    else:
        suitable_signing_servers = [
            s
            for s in signing_servers[cert_type]
            if set(signing_formats) & set(s.formats)
        ]

    if raise_on_empty_list and not suitable_signing_servers:
        raise SigningScriptError(
            f"No signing servers found with cert type {cert_type} and formats {signing_formats}"
        )
    else:
        return suitable_signing_servers


# build_signtool_cmd {{{1
def build_signtool_cmd(context, from_, fmt, to=None, servers=None):
    """Generate a signtool command to run.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with
        to (str, optional): the target path to sign to. If None, overwrite
            `from_`. Defaults to None.

    Returns:
        list: the signtool command to run.

    """
    to = to or from_
    work_dir = context.config["work_dir"]
    token = os.path.join(work_dir, "token")
    nonce = os.path.join(work_dir, "nonce")
    cert_type = task.task_cert_type(context)
    ssl_cert = context.config["ssl_cert"]
    signtool = context.config["signtool"]
    if not isinstance(signtool, (list, tuple)):
        signtool = [signtool]
    cmd = signtool + ["-n", nonce, "-t", token, "-c", ssl_cert]
    for s in get_suitable_signing_servers(context.signing_servers, cert_type, [fmt]):
        cmd.extend(["-H", s.server])
    cmd.extend(["-f", fmt])
    cmd.extend(["-o", to, from_])
    return cmd


# sign_file {{{1
async def sign_file(context, from_, fmt, to=None):
    """Send the file to signtool or autograph to be signed.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with
        to (str, optional): the target path to sign to. If None, overwrite
            `from_`. Defaults to None.

    Raises:
        FailedSubprocess: on subprocess error while signing.

    Returns:
        str: the path to the signed file

    """
    if utils.is_autograph_signing_format(fmt):
        log.info(
            "sign_file(): signing %s with %s... using autograph /sign/file", from_, fmt
        )
        await sign_file_with_autograph(context, from_, fmt, to=to)
    else:
        log.info("sign_file(): signing %s with %s... using signing server", from_, fmt)
        cmd = build_signtool_cmd(context, from_, fmt, to=to)
        await utils.execute_subprocess(cmd)
    return to or from_


# sign_gpg {{{1
async def sign_gpg(context, from_, fmt):
    """Create a detached armored signature with the gpg key.

    Because this function returns a list, gpg must be the final signing format.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        list: the path to the signed file, and sig.

    """
    to = f"{from_}.asc"
    await sign_file(context, from_, fmt, to=to)
    return [from_, to]


# sign_jar {{{1
async def sign_jar(context, from_, fmt):
    """Sign an apk, and zipalign.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed file

    """
    await sign_file(context, from_, fmt)
    await zip_align_apk(context, from_)
    return from_


# sign_macapp {{{1
async def sign_macapp(context, from_, fmt):
    """Sign a macapp.

    If given a dmg, convert to a tar.gz file first, then sign the internals.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed file

    """
    file_base, file_extension = os.path.splitext(from_)
    if file_extension == ".dmg":
        await _convert_dmg_to_tar_gz(context, from_)
        from_ = "{}.tar.gz".format(file_base)
    await sign_file(context, from_, fmt)
    return from_


# sign_signcode {{{1
async def sign_signcode(context, orig_path, fmt):
    """Sign a zipfile with authenticode.

    Extract the zip and only sign unsigned files that don't match certain
    patterns (see `_should_sign_windows`). Then recreate the zip.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed zip

    """
    file_base, file_extension = os.path.splitext(orig_path)
    # This will get cleaned up when we nuke `work_dir`. Clean up at that point
    # rather than immediately after `sign_signcode`, to optimize task runtime
    # speed over disk space.
    tmp_dir = None
    # Extract the zipfile
    if file_extension == ".zip":
        tmp_dir = tempfile.mkdtemp(prefix="zip", dir=context.config["work_dir"])
        files = await _extract_zipfile(context, orig_path, tmp_dir=tmp_dir)
    else:
        files = [orig_path]
    files_to_sign = [file for file in files if _should_sign_windows(file)]
    if not files_to_sign:
        raise SigningScriptError(
            "Did not find any files to sign, all files: {}".format(files)
        )
    # Sign the appropriate inner files
    for from_ in files_to_sign:
        await sign_file(context, from_, fmt)
    if file_extension == ".zip":
        # Recreate the zipfile
        await _create_zipfile(context, orig_path, files, tmp_dir=tmp_dir)
    return orig_path


# sign_langpack {{{1
async def sign_langpack(context, orig_path, fmt):
    """Sign language packs with autograph.

    This validates both the file extension and the language pack ID is sane.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed xpi

    """
    file_base, file_extension = os.path.splitext(orig_path)

    if not file_extension == ".xpi":
        raise SigningScriptError("Expected a .xpi")

    id = _langpack_id(orig_path)
    log.info("Identified {} as extension id: {}".format(orig_path, id))
    # Sign the appropriate inner files
    await sign_file_with_autograph(context, orig_path, fmt, extension_id=id)
    return orig_path


# sign_widevine {{{1
async def sign_widevine(context, orig_path, fmt):
    """Call the appropriate helper function to do widevine signing.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Raises:
        SigningScriptError: on unknown suffix.

    Returns:
        str: the path to the signed archive

    """
    file_base, file_extension = os.path.splitext(orig_path)
    # Convert dmg to tarball
    if file_extension == ".dmg":
        await _convert_dmg_to_tar_gz(context, orig_path)
        orig_path = "{}.tar.gz".format(file_base)
    ext_to_fn = {
        ".zip": sign_widevine_zip,
        ".tar.bz2": sign_widevine_tar,
        ".tar.gz": sign_widevine_tar,
    }
    for ext, signing_func in ext_to_fn.items():
        if orig_path.endswith(ext):
            return await signing_func(context, orig_path, fmt)
    raise SigningScriptError("Unknown widevine file format for {}".format(orig_path))


# sign_widevine_zip {{{1
async def sign_widevine_zip(context, orig_path, fmt):
    """Sign the internals of a zipfile with the widevine key.

    Extract the files to sign (see `_WIDEVINE_BLESSED_FILENAMES` and
    `_WIDEVINE_UNBLESSED_FILENAMES), skipping already-signed files.
    The blessed files should be signed with the `widevine_blessed` format.
    Then append the sigfiles to the zipfile.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed archive

    """
    # This will get cleaned up when we nuke `work_dir`. Clean up at that point
    # rather than immediately after `sign_widevine`, to optimize task runtime
    # speed over disk space.
    tmp_dir = tempfile.mkdtemp(prefix="wvzip", dir=context.config["work_dir"])
    # Get file list
    all_files = await _get_zipfile_files(orig_path)
    files_to_sign = _get_widevine_signing_files(all_files)
    is_autograph = utils.is_autograph_signing_format(fmt)
    log.debug("Widevine files to sign: %s", files_to_sign)
    if files_to_sign:
        # Extract all files so we can create `precomplete` with the full
        # file list
        all_files = await _extract_zipfile(context, orig_path, tmp_dir=tmp_dir)
        tasks = []
        # Sign the appropriate inner files
        for from_, fmt in files_to_sign.items():
            from_ = os.path.join(tmp_dir, from_)
            to = f"{from_}.sig"
            if is_autograph:
                tasks.append(
                    asyncio.ensure_future(
                        sign_widevine_with_autograph(
                            context, from_, "blessed" in fmt, to=to
                        )
                    )
                )
            else:
                tasks.append(
                    asyncio.ensure_future(sign_file(context, from_, fmt, to=to))
                )
            all_files.append(to)
        await raise_future_exceptions(tasks)
        remove_extra_files(tmp_dir, all_files)
        # Regenerate the `precomplete` file, which is used for cleanup before
        # applying a complete mar.
        _run_generate_precomplete(context, tmp_dir)
        await _create_zipfile(context, orig_path, all_files, mode="w", tmp_dir=tmp_dir)
    return orig_path


# sign_widevine_tar {{{1
async def sign_widevine_tar(context, orig_path, fmt):
    """Sign the internals of a tarfile with the widevine key.

    Extract the entire tarball, but only sign a handful of files (see
    `_WIDEVINE_BLESSED_FILENAMES` and `_WIDEVINE_UNBLESSED_FILENAMES).
    The blessed files should be signed with the `widevine_blessed` format.
    Then recreate the tarball.

    Ideally we would be able to append the sigfiles to the original tarball,
    but that's not possible with compressed tarballs.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed archive

    """
    _, compression = os.path.splitext(orig_path)
    # This will get cleaned up when we nuke `work_dir`. Clean up at that point
    # rather than immediately after `sign_widevine`, to optimize task runtime
    # speed over disk space.
    tmp_dir = tempfile.mkdtemp(prefix="wvtar", dir=context.config["work_dir"])
    # Get file list
    all_files = await _get_tarfile_files(orig_path, compression)
    files_to_sign = _get_widevine_signing_files(all_files)
    is_autograph = utils.is_autograph_signing_format(fmt)
    log.debug("Widevine files to sign: %s", files_to_sign)
    if files_to_sign:
        # Extract all files so we can create `precomplete` with the full
        # file list
        all_files = await _extract_tarfile(
            context, orig_path, compression, tmp_dir=tmp_dir
        )
        tasks = []
        # Sign the appropriate inner files
        for from_, fmt in files_to_sign.items():
            from_ = os.path.join(tmp_dir, from_)
            # Don't try to sign directories
            if not os.path.isfile(from_):
                continue
            # Move the sig location on mac. This should be noop on linux.
            to = _get_mac_sigpath(from_)
            log.debug("Adding %s to the sigfile paths...", to)
            makedirs(os.path.dirname(to))
            if is_autograph:
                tasks.append(
                    asyncio.ensure_future(
                        sign_widevine_with_autograph(
                            context, from_, "blessed" in fmt, to=to
                        )
                    )
                )
            else:
                tasks.append(
                    asyncio.ensure_future(sign_file(context, from_, fmt, to=to))
                )
            all_files.append(to)
        await raise_future_exceptions(tasks)
        remove_extra_files(tmp_dir, all_files)
        # Regenerate the `precomplete` file, which is used for cleanup before
        # applying a complete mar.
        _run_generate_precomplete(context, tmp_dir)
        await _create_tarfile(
            context, orig_path, all_files, compression, tmp_dir=tmp_dir
        )
    return orig_path


# sign_omnija {{{1
async def sign_omnija(context, orig_path, fmt):
    """Call the appropriate helper function to do omnija signing.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Raises:
        SigningScriptError: on unknown suffix.

    Returns:
        str: the path to the signed archive

    """
    file_base, file_extension = os.path.splitext(orig_path)
    # Convert dmg to tarball
    if file_extension == ".dmg":
        await _convert_dmg_to_tar_gz(context, orig_path)
        orig_path = "{}.tar.gz".format(file_base)
    ext_to_fn = {
        ".zip": sign_omnija_zip,
        ".tar.bz2": sign_omnija_tar,
        ".tar.gz": sign_omnija_tar,
    }
    for ext, signing_func in ext_to_fn.items():
        if orig_path.endswith(ext):
            return await signing_func(context, orig_path, fmt)
    raise SigningScriptError("Unknown omnija file format for {}".format(orig_path))


# sign_omnija_zip {{{1
async def sign_omnija_zip(context, orig_path, fmt):
    """Sign the internals of a zipfile with the omnija key for all omni.ja files.

    Extract the files to sign, then sign them with autograph, recreating the omni.ja
    from the original to preserve performance tweeks but adding signing info,
    Then append the sigfiles to the zipfile.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed archive

    """
    # This will get cleaned up when we nuke `work_dir`. Clean up at that point
    # rather than immediately after `sign_omnija`, to optimize task runtime
    # speed over disk space.
    tmp_dir = tempfile.mkdtemp(prefix="ojzip", dir=context.config["work_dir"])
    # Get file list
    all_files = await _get_zipfile_files(orig_path)
    files_to_sign = _get_omnija_signing_files(all_files)
    log.debug("Omnija files to sign: %s", files_to_sign)
    if files_to_sign:
        all_files = await _extract_zipfile(context, orig_path, tmp_dir=tmp_dir)
        tasks = []
        # Sign the appropriate inner files
        for from_, fmt in files_to_sign.items():
            from_ = os.path.join(tmp_dir, from_)
            tasks.append(
                asyncio.ensure_future(sign_omnija_with_autograph(context, from_))
            )
        await raise_future_exceptions(tasks)
        await _create_zipfile(context, orig_path, all_files, mode="w", tmp_dir=tmp_dir)
    return orig_path


# sign_omnija_tar {{{1
async def sign_omnija_tar(context, orig_path, fmt):
    """Sign the internals of a tarfile with the omnija key for all omni.ja files.

    Extract the files to sign, then sign them with autograph, recreating the omni.ja
    from the original to preserve performance tweeks but adding signing info.
    Then recreate the tarball.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed archive

    """
    _, compression = os.path.splitext(orig_path)
    # This will get cleaned up when we nuke `work_dir`. Clean up at that point
    # rather than immediately after `sign_widevine`, to optimize task runtime
    # speed over disk space.
    tmp_dir = tempfile.mkdtemp(prefix="ojtar", dir=context.config["work_dir"])
    # Get file list
    all_files = await _get_tarfile_files(orig_path, compression)
    files_to_sign = _get_omnija_signing_files(all_files)
    log.debug("Omnija files to sign: %s", files_to_sign)
    if files_to_sign:
        # Extract all files so we can create `precomplete` with the full
        # file list
        all_files = await _extract_tarfile(
            context, orig_path, compression, tmp_dir=tmp_dir
        )
        tasks = []
        # Sign the appropriate inner files
        for from_, fmt in files_to_sign.items():
            from_ = os.path.join(tmp_dir, from_)
            # Don't try to sign directories
            if not os.path.isfile(from_):
                continue
            tasks.append(
                asyncio.ensure_future(sign_omnija_with_autograph(context, from_))
            )
        await raise_future_exceptions(tasks)
        await _create_tarfile(
            context, orig_path, all_files, compression, tmp_dir=tmp_dir
        )
    return orig_path


# _should_sign_windows {{{1
def _should_sign_windows(filename):
    """Return True if filename should be signed."""
    # These should already be signed by Microsoft.
    _dont_sign = [
        "D3DCompiler_42.dll",
        "d3dx9_42.dll",
        "D3DCompiler_43.dll",
        "d3dx9_43.dll",
        "msvc*.dll",
    ]
    ext = os.path.splitext(filename)[1]
    b = os.path.basename(filename)
    if ext in (".dll", ".exe", ".msi", ".bin") and not any(
        fnmatch.fnmatch(b, p) for p in _dont_sign
    ):
        return True
    return False


def _langpack_id(filename):
    """Return a list of id's for the langpacks.

    Side Affect of checking if filenames are actually langpacks.
    """
    langpack = zipfile.ZipFile(filename, "r")
    id = None
    with langpack.open("manifest.json", "r") as f:
        manifest = json.load(f)
        if not (
            "languages" in manifest
            and "langpack_id" in manifest
            and "applications" in manifest
            and "gecko" in manifest["applications"]
            and "id" in manifest["applications"]["gecko"]
            and LANGPACK_RE.match(manifest["applications"]["gecko"]["id"])
        ):
            raise SigningScriptError("{} is not a valid langpack".format(filename))
        id = manifest["applications"]["gecko"]["id"]
    return id


# _get_mac_sigpath {{{1
def _get_mac_sigpath(from_):
    """For mac paths, replace the final Contents/MacOS/ with Contents/Resources/."""
    to = from_
    if "Contents/MacOS" in from_:
        parts = from_.split("/")
        parts.reverse()
        i = parts.index("MacOS")
        parts[i] = "Resources"
        parts.reverse()
        to = "/".join(parts)
        log.debug("Sigfile for {} should be {}.sig".format(from_, to))
    return "{}.sig".format(to)


# _get_widevine_signing_files {{{1
def _get_widevine_signing_files(file_list):
    """Return a dict of path:signing_format for each path to be signed."""
    files = {}
    for filename in file_list:
        fmt = None
        base_filename = os.path.basename(filename)
        if base_filename in _WIDEVINE_BLESSED_FILENAMES:
            fmt = "widevine_blessed"
        elif base_filename in _WIDEVINE_NONBLESSED_FILENAMES:
            fmt = "widevine"
        if fmt:
            log.debug("Found {} to sign {}".format(filename, fmt))
            sigpath = _get_mac_sigpath(filename)
            if sigpath not in file_list:
                files[filename] = fmt
            else:
                log.debug("{} is already signed! Skipping...".format(filename))
    return files


# _get_omnija_signing_files {{{1
def _get_omnija_signing_files(file_list):
    """Return a dict of path:signing_format for each path to be signed."""
    files = {}
    for filename in file_list:
        fmt = None
        base_filename = os.path.basename(filename)
        if base_filename in {"omni.ja"}:
            fmt = "omnija"
        if fmt:
            log.debug("Found {} to sign {}".format(filename, fmt))
            files[filename] = fmt
    return files


# _run_generate_precomplete {{{1
def _run_generate_precomplete(context, tmp_dir):
    """Regenerate `precomplete` file with widevine sig paths for complete mar."""
    log.info("Generating `precomplete` file...")
    path = _ensure_one_precomplete(tmp_dir, "before")
    with open(path, "r") as fh:
        before = fh.readlines()
    generate_precomplete(os.path.dirname(path))
    path = _ensure_one_precomplete(tmp_dir, "after")
    with open(path, "r") as fh:
        after = fh.readlines()
    # Create diff file
    diff_path = os.path.join(context.config["work_dir"], "precomplete.diff")
    with open(diff_path, "w") as fh:
        for line in difflib.ndiff(before, after):
            fh.write(line)
    utils.copy_to_dir(
        diff_path, context.config["artifact_dir"], target="public/logs/precomplete.diff"
    )


# _ensure_one_precomplete {{{1
def _ensure_one_precomplete(tmp_dir, adj):
    """Ensure we only have one `precomplete` file in `tmp_dir`."""
    return get_single_item_from_sequence(
        glob.glob(os.path.join(tmp_dir, "**", "precomplete"), recursive=True),
        condition=lambda _: True,
        ErrorClass=SigningScriptError,
        no_item_error_message='No `precomplete` file found in "{}"'.format(tmp_dir),
        too_many_item_error_message='More than one `precomplete` file {} in "{}"'.format(
            adj, tmp_dir
        ),
    )


# remove_extra_files {{{1
def remove_extra_files(top_dir, file_list):
    """Find any extra files in `top_dir`, given an expected `file_list`.

    Args:
        top_dir (str): the dir to walk
        file_list (list): the list of expected files

    Returns:
        list: the list of extra files

    """
    all_files = [
        os.path.realpath(f)
        for f in glob.glob(os.path.join(top_dir, "**", "*"), recursive=True)
    ]
    good_files = [os.path.realpath(f) for f in file_list]
    extra_files = list(set(all_files) - set(good_files))
    for f in extra_files:
        if os.path.isfile(f):
            log.warning("Extra file to clean up: {}".format(f))
            rm(f)
    return extra_files


# zip_align_apk {{{1
async def zip_align_apk(context, abs_to):
    """Optimize APK for better run-time performance.

    This is necessary if the APK is uploaded to the Google Play Store.
    https://developer.android.com/studio/command-line/zipalign.html

    Args:
        context (Context): the signing context
        abs_to (str): the absolute path to the apk

    """
    original_apk_location = abs_to
    zipalign_executable_location = context.config["zipalign"]

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_apk_location = os.path.join(temp_dir, "aligned.apk")

        zipalign_command = [zipalign_executable_location]
        if context.config["verbose"] is True:
            zipalign_command += ["-v"]

        zipalign_command += [_ZIP_ALIGNMENT, original_apk_location, temp_apk_location]
        await utils.execute_subprocess(zipalign_command)
        shutil.move(temp_apk_location, abs_to)

    log.info('"{}" has been zip aligned'.format(abs_to))


# _convert_dmg_to_tar_gz {{{1
async def _convert_dmg_to_tar_gz(context, from_):
    """Explode a dmg and tar up its contents. Return the relative tarball path."""
    work_dir = context.config["work_dir"]
    abs_from = os.path.join(work_dir, from_)
    # replace .dmg suffix with .tar.gz (case insensitive)
    to = re.sub(r"\.dmg$", ".tar.gz", from_, flags=re.I)
    abs_to = os.path.join(work_dir, to)
    dmg_executable_location = context.config["dmg"]
    hfsplus_executable_location = context.config["hfsplus"]

    with tempfile.TemporaryDirectory() as temp_dir:
        app_dir = os.path.join(temp_dir, "app")
        utils.mkdir(app_dir)
        undmg_cmd = [dmg_executable_location, "extract", abs_from, "tmp.hfs"]
        await utils.execute_subprocess(undmg_cmd, cwd=temp_dir, log_level=logging.DEBUG)
        hfsplus_cmd = [
            hfsplus_executable_location,
            "tmp.hfs",
            "extractall",
            "/",
            app_dir,
        ]
        await utils.execute_subprocess(
            hfsplus_cmd, cwd=temp_dir, log_level=logging.DEBUG
        )
        tar_cmd = ["tar", "czf", abs_to, "."]
        await utils.execute_subprocess(tar_cmd, cwd=app_dir)

    return to


# _get_zipfile_files {{{1
async def _get_zipfile_files(from_):
    with zipfile.ZipFile(from_, mode="r") as z:
        files = z.namelist()
        return files


# _extract_zipfile {{{1
async def _extract_zipfile(context, from_, files=None, tmp_dir=None):
    work_dir = context.config["work_dir"]
    tmp_dir = tmp_dir or os.path.join(work_dir, "unzipped")
    log.debug(
        "Extracting {} from {} to {}...".format(files or "all files", from_, tmp_dir)
    )
    try:
        extracted_files = []
        rm(tmp_dir)
        utils.mkdir(tmp_dir)
        with zipfile.ZipFile(from_, mode="r") as z:
            if files is not None:
                for name in files:
                    z.extract(name, path=tmp_dir)
                    extracted_files.append(os.path.join(tmp_dir, name))
            else:
                for name in z.namelist():
                    extracted_files.append(os.path.join(tmp_dir, name))
                z.extractall(path=tmp_dir)
        return extracted_files
    except Exception as e:
        raise SigningScriptError(e)


# _create_zipfile {{{1
async def _create_zipfile(context, to, files, tmp_dir=None, mode="w"):
    work_dir = context.config["work_dir"]
    tmp_dir = tmp_dir or os.path.join(work_dir, "unzipped")
    try:
        log.info("Creating zipfile {}...".format(to))
        with zipfile.ZipFile(to, mode=mode, compression=zipfile.ZIP_DEFLATED) as z:
            for f in files:
                relpath = os.path.relpath(f, tmp_dir)
                z.write(f, arcname=relpath)
        return to
    except Exception as e:
        raise SigningScriptError(e)


# _get_tarfile_compression {{{1
def _get_tarfile_compression(compression):
    compression = compression.lstrip(".")
    if compression not in ("bz2", "gz"):
        raise SigningScriptError(
            "{} not a supported tarfile compression format!".format(compression)
        )
    return compression


# _get_tarfile_files {{{1
async def _get_tarfile_files(from_, compression):
    compression = _get_tarfile_compression(compression)
    with tarfile.open(from_, mode="r:{}".format(compression)) as t:
        files = t.getnames()
        return files


# _extract_tarfile {{{1
async def _extract_tarfile(context, from_, compression, tmp_dir=None):
    work_dir = context.config["work_dir"]
    tmp_dir = tmp_dir or os.path.join(work_dir, "untarred")
    compression = _get_tarfile_compression(compression)
    try:
        files = []
        rm(tmp_dir)
        utils.mkdir(tmp_dir)
        with tarfile.open(from_, mode="r:{}".format(compression)) as t:
            t.extractall(path=tmp_dir)
            for name in t.getnames():
                path = os.path.join(tmp_dir, name)
                os.path.isfile(path) and files.append(path)
        return files
    except Exception as e:
        raise SigningScriptError(e)


# _owner_filter {{{1
def _owner_filter(tarinfo_obj):
    """Force file ownership to be root, Bug 1473850."""
    tarinfo_obj.uid = 0
    tarinfo_obj.gid = 0
    tarinfo_obj.uname = ""
    tarinfo_obj.gname = ""
    return tarinfo_obj


# _create_tarfile {{{1
async def _create_tarfile(context, to, files, compression, tmp_dir=None):
    work_dir = context.config["work_dir"]
    tmp_dir = tmp_dir or os.path.join(work_dir, "untarred")
    compression = _get_tarfile_compression(compression)
    try:
        log.info("Creating tarfile {}...".format(to))
        with tarfile.open(to, mode="w:{}".format(compression)) as t:
            for f in files:
                relpath = os.path.relpath(f, tmp_dir)
                t.add(f, arcname=relpath, filter=_owner_filter)
        return to
    except Exception as e:
        raise SigningScriptError(e)


async def call_autograph(url, user, password, request_json):
    """Call autograph and return the json response."""
    auth = HawkAuth(id=user, key=password)
    with requests.Session() as session:
        r = session.post(url, json=request_json, auth=auth)
        log.debug(
            "Autograph response: %s", r.text[:120] if len(r.text) >= 120 else r.text
        )
        r.raise_for_status()
        return r.json()


def make_signing_req(input_bytes, server, fmt, keyid=None, extension_id=None):
    """Make a signing request object to pass to autograph."""
    base64_input = base64.b64encode(input_bytes).decode("ascii")
    sign_req = {"input": base64_input}

    if keyid:
        sign_req["keyid"] = keyid

    # TODO: Is this the right place to do this?
    if utils.is_apk_autograph_signing_format(fmt):
        # We don't want APKs to have their compression changed
        sign_req["options"] = {"zip": "passthrough"}

        if utils.is_sha1_apk_autograph_signing_format(fmt):
            # We ask for a SHA1 digest from Autograph
            # https://github.com/mozilla-services/autograph/pull/166/files
            sign_req["options"]["pkcs7_digest"] = "SHA1"

    if "omnija" in fmt or "langpack" in fmt:
        sign_req.setdefault("options", {})
        # https://bugzilla.mozilla.org/show_bug.cgi?id=1533818#c9
        sign_req["options"]["id"] = extension_id
        sign_req["options"]["cose_algorithms"] = ["ES256"]
        sign_req["options"]["pkcs7_digest"] = "SHA256"

    return [sign_req]


async def sign_with_autograph(
    server, input_bytes, fmt, autograph_method, keyid=None, extension_id=None
):
    """Signs data with autograph and returns the result.

    Args:
        server (SigningServer): the server to connect to sign
        input_bytes (bytes): the source data to sign
        fmt (str): the format to sign with
        autograph_method (str): which autograph method to use to sign. must be
                                one of 'file', 'hash', or 'data'
        keyid (str): which key to use on autograph (optional)
        extension_id (str): which id to send to autograph for the extension (optional)

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        bytes: the signed data

    """
    if autograph_method not in {"file", "hash", "data"}:
        raise SigningScriptError(f"Unsupported autograph method: {autograph_method}")

    sign_req = make_signing_req(input_bytes, server, fmt, keyid, extension_id)

    log.debug("signing data with format %s with %s", fmt, autograph_method)

    url = f"{server.server}/sign/{autograph_method}"

    sign_resp = await retry_async(
        call_autograph,
        args=(url, server.user, server.password, sign_req),
        attempts=3,
        sleeptime_kwargs={"delay_factor": 2.0},
    )

    if autograph_method == "file":
        return sign_resp[0]["signed_file"]
    else:
        return sign_resp[0]["signature"]


async def sign_file_with_autograph(context, from_, fmt, to=None, extension_id=None):
    """Signs file with autograph and writes the results to a file.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with
        to (str, optional): the target path to sign to. If None, overwrite
                            `from_`. Defaults to None.
        extension_id (str, optional): the extension id to use when signing.

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        str: the path to the signed file

    """
    if not utils.is_autograph_signing_format(fmt):
        raise SigningScriptError(f"Not an autograph format: {fmt}")
    cert_type = task.task_cert_type(context)
    servers = get_suitable_signing_servers(
        context.signing_servers, cert_type, [fmt], raise_on_empty_list=True
    )
    s = servers[0]
    to = to or from_
    input_bytes = open(from_, "rb").read()
    signed_bytes = base64.b64decode(
        await sign_with_autograph(
            s, input_bytes, fmt, "file", extension_id=extension_id
        )
    )
    with open(to, "wb") as fout:
        fout.write(signed_bytes)
    return to


async def sign_gpg_with_autograph(context, from_, fmt):
    """Signs file with autograph and writes the results to a file.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        list: the path to the signed file, and sig.

    """
    if not utils.is_autograph_signing_format(fmt):
        raise SigningScriptError(f"Not an autograph format: {fmt}")
    cert_type = task.task_cert_type(context)
    servers = get_suitable_signing_servers(
        context.signing_servers, cert_type, [fmt], raise_on_empty_list=True
    )
    s = servers[0]
    to = f"{from_}.asc"
    input_bytes = open(from_, "rb").read()
    signature = await sign_with_autograph(s, input_bytes, fmt, "data")
    with open(to, "w") as fout:
        fout.write(signature)
    return [from_, to]


async def sign_hash_with_autograph(context, hash_, fmt, keyid=None):
    """Signs hash with autograph and returns the result.

    Args:
        context (Context): the signing context
        hash_ (bytes): the input hash to sign
        fmt (str): the format to sign with
        keyid (str): which key to use on autograph (optional)

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        bytes: the signature

    """
    if not utils.is_autograph_signing_format(fmt):
        raise SigningScriptError(f"Not an autograph format: {fmt}")
    cert_type = task.task_cert_type(context)
    servers = get_suitable_signing_servers(
        context.signing_servers, cert_type, [fmt], raise_on_empty_list=True
    )
    s = servers[0]
    signature = base64.b64decode(
        await sign_with_autograph(s, hash_, fmt, "hash", keyid)
    )
    return signature


def get_mar_verification_key(cert_type, fmt, keyid):
    """Get the public key file for the format/cert_type.

    Args:
        cert_type (str): the cert scope string
        fmt (str): the signing format
        keyid (str): the key id to use (can be None)

    Raises:
        SigningScriptError: if no key is found

    Returns:
        str: the public key to use with ``-k``

    """
    # Cert types are like ...
    cert_type = cert_type.split(":")[-1]
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    try:
        if keyid is None:
            return os.path.join(data_dir, _DEFAULT_MAR_VERIFY_KEYS[fmt][cert_type])
        else:
            # Make sure you can't try and read outside of the data directory
            if "/" in keyid:
                raise SigningScriptError("/ not allowed in keyids")
            keyid = os.path.basename(keyid)
            return os.path.join(data_dir, f"{keyid}.pem")
    except KeyError as err:
        raise SigningScriptError(
            f"Can't find mar verify key for {fmt}, {cert_type} ({keyid}):\n{err}"
        )


def verify_mar_signature(cert_type, fmt, mar, keyid=None):
    """Verify a mar signature, via mardor.

    Args:
        cert_type (str): the cert scope string
        fmt (str): the signing format
        mar (str): the path to the mar file
        keyid (str, optional): the key id to use (can be None)

    Raises:
        SigningScriptError: if the signature doesn't verify, or the nick isn't found

    """
    mar_verify_key = get_mar_verification_key(cert_type, fmt, keyid)
    try:
        mar_path = os.path.join(os.path.dirname(sys.executable), "mar")
        cmd = [mar_path, "-k", mar_verify_key, "-v", mar]
        log.info("Running %s", cmd)
        subprocess.check_call(cmd, stdout=sys.stdout, stderr=sys.stderr)
        log.info("Verified signature.")
    except subprocess.CalledProcessError as e:
        raise SigningScriptError(e)


async def sign_mar384_with_autograph_hash(context, from_, fmt, to=None):
    """Signs a hash with autograph, injects it into the file, and writes the result to arg `to` or `from_` if `to` is None.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with
        to (str, optional): the target path to sign to. If None, overwrite
            `from_`. Defaults to None.

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        str: the path to the signed file

    """
    cert_type = task.task_cert_type(context)
    # Get any key id that the task may have specified
    fmt, keyid = utils.split_autograph_format(fmt)
    # Call to check that we have a server available
    get_suitable_signing_servers(
        context.signing_servers, cert_type, [fmt], raise_on_empty_list=True
    )

    hash_algo, expected_signature_length = "sha384", 512

    # Add a dummy signature into a temporary file (TODO: dedup with mardor.cli do_hash)
    with tempfile.TemporaryFile() as tmp:
        with open(from_, "rb") as f:
            add_signature_block(f, tmp, hash_algo)

        tmp.seek(0)

        with MarReader(tmp) as m:
            hashes = m.calculate_hashes()
        h = hashes[0][1]

    signature = await sign_hash_with_autograph(context, h, fmt, keyid)

    # Add a signature to the MAR file (TODO: dedup with mardor.cli do_add_signature)
    if len(signature) != expected_signature_length:
        raise SigningScriptError(
            "signed mar hash signature has invalid length for hash algo {}. Got {} expected {}.".format(
                hash_algo, len(signature), expected_signature_length
            )
        )

    # use the tmp file in case param `to` is `from_` which causes stream errors
    tmp_dst = tempfile.NamedTemporaryFile(mode="w+b", delete=False)
    with open(tmp_dst.name, "w+b") as dst:
        with open(from_, "rb") as src:
            add_signature_block(src, dst, hash_algo, signature)

    to = to or from_
    shutil.copyfile(tmp_dst.name, to)
    os.unlink(tmp_dst.name)

    verify_mar_signature(cert_type, fmt, to, keyid)

    log.info("wrote mar with autograph signed hash %s to %s", from_, to)
    return to


async def sign_widevine_with_autograph(context, from_, blessed, to=None):
    """Create a widevine signature using autograph as a backend.

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign
        fmt (str): the format to sign with
        blessed (bool): whether to use blessed signing or not
        to (str, optional): the target path to sign to. If None, write to
            `{from_}.sig`. Defaults to None.

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        str: the path to the signature file

    """
    if not widevine:
        raise ImportError("widevine module not available")

    to = to or f"{from_}.sig"
    flags = 1 if blessed else 0
    fmt = "autograph_widevine"

    h = widevine.generate_widevine_hash(from_, flags)

    signature = await sign_hash_with_autograph(context, h, fmt)

    with open(to, "wb") as fout:
        certificate = open(context.config["widevine_cert"], "rb").read()
        sig = widevine.generate_widevine_signature(signature, certificate, flags)
        fout.write(sig)
    return to


async def sign_omnija_with_autograph(context, from_):
    """Sign the omnija file specified using autograph.

    This function overwrites from_
    rebuild it using the signed meta-data and the original omni.ja
    in order to facilitate the performance wins we do as part of the build

    Args:
        context (Context): the signing context
        from_ (str): the source file to sign (overwrites)

    Raises:
        Requests.RequestException: on failure
        SigningScriptError: when no suitable signing server is found for fmt

    Returns:
        str: the path to the signature file

    """
    signed_out = tempfile.mkstemp(
        prefix="oj_signed", suffix=".ja", dir=context.config["work_dir"]
    )[1]
    merged_out = tempfile.mkstemp(
        prefix="oj_merged", suffix=".ja", dir=context.config["work_dir"]
    )[1]

    await sign_file_with_autograph(
        context,
        from_,
        "autograph_omnija",
        to=signed_out,
        extension_id="omni.ja@mozilla.org",
    )
    await merge_omnija_files(orig=from_, signed=signed_out, to=merged_out)
    with open(from_, "wb") as fout:
        with open(merged_out, "rb") as fin:
            fout.write(fin.read())
    return from_


async def merge_omnija_files(orig, signed, to):
    """Merge multiple omnijar files together.

    This takes the original file, and reads it in, including performance
    characteristics (e.g. jarlog ordering for preloading),
    then adds data from the "signed" copy (the META-INF folder)
    and finally writes it all out to a new omni.ja file.

    Args:
        context (Context): the signing context
        orig (str): the source file to sign
        signed (str): the signed file, without optimizations
        to (str): the output path for the merge

    Returns:
        bool: always True if function succeeded.

    """
    orig_jarreader = mozjar.JarReader(orig)
    with mozjar.JarWriter(to, compress=orig_jarreader.compression) as to_writer:
        for origjarfile in orig_jarreader:
            to_writer.add(
                origjarfile.filename, origjarfile, compress=origjarfile.compress
            )
        # Use ZipFile here because mozjar can't read the signed copies
        signed_zip = zipfile.ZipFile(signed, "r")
        for fname in signed_zip.namelist():
            if fname.startswith("META-INF"):
                to_writer.add(fname, signed_zip.open(fname, "r"))
        if orig_jarreader.last_preloaded:
            jarlog = list(orig_jarreader.entries.keys())
            preloads = jarlog[: jarlog.index(orig_jarreader.last_preloaded) + 1]
            to_writer.preload(preloads)
    return True


# sign_authenticode_file {{{1
async def sign_authenticode_file(context, orig_path, fmt):
    """Sign a file in-place with authenticode, using autograph as a backend.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        True on success, False otherwise

    """
    loop = asyncio.get_event_loop()

    if winsign.sign.is_signed(orig_path):
        log.info("%s is already signed", orig_path)
        return True

    def signer(digest, digest_algo):
        thread_loop = asyncio.new_event_loop()
        try:
            return thread_loop.run_until_complete(
                sign_hash_with_autograph(context, digest, fmt)
            )
        except Exception:
            log.exception("Error signing authenticode hash with autograph")
            raise

    def sign_file():
        infile = orig_path
        outfile = orig_path + "-new"
        digest_algo = "sha1"
        certs = load_pem_certs(open(context.config["authenticode_cert"], "rb").read())
        url = context.config["authenticode_url"]
        timestamp_style = context.config["authenticode_timestamp_style"]
        if fmt.endswith("authenticode_stub"):
            crosscert = context.config["authenticode_cross_cert"]
        else:
            crosscert = None

        if not winsign.sign.sign_file(
            infile,
            outfile,
            digest_algo,
            certs,
            signer,
            url=url,
            crosscert=crosscert,
            timestamp_style=timestamp_style,
        ):
            raise IOError(f"Couldn't sign {orig_path}")
        os.rename(outfile, infile)

    return await loop.run_in_executor(None, sign_file)


# sign_authenticode_zip {{{1
async def sign_authenticode_zip(context, orig_path, fmt):
    """Sign a zipfile with authenticode, using autograph as a backend.

    Extract the zip and only sign unsigned files that don't match certain
    patterns (see `_should_sign_windows`). Then recreate the zip.

    Args:
        context (Context): the signing context
        orig_path (str): the source file to sign
        fmt (str): the format to sign with

    Returns:
        str: the path to the signed zip

    """
    file_base, file_extension = os.path.splitext(orig_path)
    # This will get cleaned up when we nuke `work_dir`. Clean up at that point
    # rather than immediately after `sign_signcode`, to optimize task runtime
    # speed over disk space.
    tmp_dir = None
    # Extract the zipfile
    if file_extension == ".zip":
        tmp_dir = tempfile.mkdtemp(prefix="zip", dir=context.config["work_dir"])
        files = await _extract_zipfile(context, orig_path, tmp_dir=tmp_dir)
    else:
        files = [orig_path]
    files_to_sign = [file for file in files if _should_sign_windows(file)]
    if not files_to_sign:
        raise SigningScriptError(
            "Did not find any files to sign, all files: {}".format(files)
        )

    # Sign the appropriate inner files
    tasks = [sign_authenticode_file(context, file_, fmt) for file_ in files_to_sign]
    await asyncio.gather(*tasks)
    if file_extension == ".zip":
        # Recreate the zipfile
        await _create_zipfile(context, orig_path, files, tmp_dir=tmp_dir)
    return orig_path
