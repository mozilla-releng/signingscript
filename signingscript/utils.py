"""Signingscript general utility functions."""
import asyncio
from asyncio.subprocess import PIPE, STDOUT
from collections import namedtuple
import functools
import hashlib
import logging
import os
from shutil import copyfile
import traceback
import yaml

from signingscript.exceptions import FailedSubprocess, SigningServerError

log = logging.getLogger(__name__)


SigningServer = namedtuple(
    "SigningServer", ["server", "user", "password", "formats", "server_type"]
)


def mkdir(path):
    """Equivalent to `mkdir -p`.

    Args:
        path (str): the path to mkdir

    """
    try:
        os.makedirs(path)
        log.info("mkdir {}".format(path))
    except OSError:
        pass


def get_hash(path, hash_type="sha512"):
    """Get the hash of a given path.

    Args:
        path (str): the path to calculate the hash for
        hash_type (str, optional): the algorithm to use.  Defaults to `sha512`

    Returns:
        str: the hexdigest of the hash

    """
    # I'd love to make this async, but evidently file i/o is always ready
    h = hashlib.new(hash_type)
    with open(path, "rb") as f:
        for chunk in iter(functools.partial(f.read, 4096), b''):
            h.update(chunk)
    return h.hexdigest()


def load_yaml(path):
    """Load yaml from path.

    Args:
        path (str): the path to read from

    Returns:
        dict: the loaded yaml object

    """
    with open(path, "r") as fh:
        return yaml.safe_load(fh)


def load_signing_server_config(context):
    """Build a specialized signing server config from the `signing_server_config`.

    Args:
        context (Context): the signing context

    Returns:
        dict of lists: keyed by signing cert type, value is a list of SigningServer named tuples

    """
    path = context.config['signing_server_config']
    log.info("Loading signing server config from {}".format(path))
    raw_cfg = load_yaml(path)

    cfg = {}
    for signing_type, pool_data in raw_cfg.items():
        for pool_nick, server_data in pool_data.items():
            cfg.setdefault(signing_type, [])
            for url in server_data['urls']:
                cfg[signing_type].append(
                    SigningServer(
                        server=url,
                        user=server_data['user'],
                        password=server_data['pass'],
                        formats=server_data['formats'],
                        server_type=server_data['server-type'],
                    )
                )
    log.info("Signing server config loaded from {}".format(path))
    return cfg


async def log_output(fh):
    """Log the output from an async generator.

    Args:
        fh (async generator): the async generator to log output from

    """
    while True:
        line = await fh.readline()
        if line:
            log.info(line.decode("utf-8").rstrip())
        else:
            break


def copy_to_dir(source, parent_dir, target=None):
    """Copy `source` to `parent_dir`, optionally renaming.

    Args:
        source (str): the source path
        parent_dir (str): the target parent dir. This doesn't have to exist
        target (str, optional): the basename of the target file.  If None,
            use the basename of `source`. Defaults to None.

    Raises:
        SigningServerError: on failure

    """
    target = target or os.path.basename(source)
    target_path = os.path.join(parent_dir, target)
    try:
        parent_dir = os.path.dirname(target_path)
        mkdir(parent_dir)
        if source != target_path:
            log.info("Copying %s to %s" % (source, target_path))
            copyfile(source, target_path)
            return target_path
        else:
            log.info("Not copying %s to itself" % (source))
    except (IOError, OSError):
        traceback.print_exc()
        raise SigningServerError("Can't copy {} to {}!".format(source, target_path))


async def execute_subprocess(command, **kwargs):
    """Execute a command in a subprocess.

    Args:
        command (list): the command to run
        **kwargs: the kwargs to pass to subprocess

    Raises:
        FailedSubprocess: on failure

    """
    message = 'Running "{}"'.format(' '.join(command))
    if 'cwd' in kwargs:
        message += " in {}".format(kwargs['cwd'])
    log.info(message)
    subprocess = await asyncio.create_subprocess_exec(
        *command, stdout=PIPE, stderr=STDOUT, **kwargs
    )
    log.info("COMMAND OUTPUT: ")
    await log_output(subprocess.stdout)
    exitcode = await subprocess.wait()
    log.info("exitcode {}".format(exitcode))

    if exitcode != 0:
        raise FailedSubprocess('Command `{}` failed'.format(' '.join(command)))


def is_autograph_signing_format(format_):
    """Return bool of whether a signing format is for autograph.

    Args:
        format_ (str): the format to check

    """
    return format_ and format_.startswith('autograph_')
