#!/bin/bash
set -e

#
# Check that all required variables exist
#
test $CONFIG_DIR
test $CONFIG_LOADER
test $COT_PRODUCT
test $PROJECT_NAME
test $PUBLIC_IP
test $TEMPLATE_DIR
test $WIDEVINE_MODULE

FILES_DIR=/app/files
export DMG_PATH=$FILES_DIR/dmg
export HFSPLUS_PATH=$FILES_DIR/hfsplus
export ZIPALIGN_PATH=/usr/bin/zipalign

export PASSWORDS_PATH=$CONFIG_DIR/passwords.json
export SIGNTOOL_PATH="/app/bin/signtool"
export SSL_CERT_PATH="/app/signingscript/data/host.cert"
export WIDEVINE_CERT_PATH=$CONFIG_DIR/widevine.crt

WIDEVINE_MODULE_PATH=/app/widevine-0.1.0-py3-none-any.whl
echo $WIDEVINE_MODULE | base64 -d > $WIDEVINE_MODULE_PATH
/app/bin/pip install -U $WIDEVINE_MODULE_PATH

case $COT_PRODUCT in
  firefox)
    ;;
  thunderbird)
    ;;
  mobile)
    ;;
  application-services)
    ;;
  *)
    exit 1
    ;;
esac

case $ENV in
  prod)
    export GPG_PUBKEY_PATH=$FILES_DIR/KEY_prod
    export WIDEVINE_CERT_PATH=$FILES_DIR/widevine_prod.crt
    ;;
  *)
    export GPG_PUBKEY_PATH=$FILES_DIR/KEY_dep
    export WIDEVINE_CERT_PATH=$FILES_DIR/widevine_dep.crt
    ;;
esac

case $ENV in
  dev)
    test $AUTOGRAPH_FENNEC_PASSWORD
    test $AUTOGRAPH_FENNEC_USERNAME
    test $AUTOGRAPH_GPG_PASSWORD
    test $AUTOGRAPH_GPG_USERNAME
    test $AUTOGRAPH_LANGPACK_PASSWORD
    test $AUTOGRAPH_LANGPACK_USERNAME
    test $AUTOGRAPH_MAR_PASSWORD
    test $AUTOGRAPH_MAR_STAGE_PASSWORD
    test $AUTOGRAPH_MAR_STAGE_USERNAME
    test $AUTOGRAPH_MAR_USERNAME
    test $AUTOGRAPH_OMNIJA_PASSWORD
    test $AUTOGRAPH_OMNIJA_USERNAME
    test $AUTOGRAPH_WIDEVINE_PASSWORD
    test $AUTOGRAPH_WIDEVINE_USERNAME
    test $SIGNING_SERVER_PASSWORD
    test $SIGNING_SERVER_USERNAME
    ;;
  fake-prod)
    case $COT_PRODUCT in
      firefox|thunderbird)
        test $AUTOGRAPH_FENNEC_PASSWORD
        test $AUTOGRAPH_FENNEC_USERNAME
        test $AUTOGRAPH_GPG_PASSWORD
        test $AUTOGRAPH_GPG_USERNAME
        test $AUTOGRAPH_LANGPACK_PASSWORD
        test $AUTOGRAPH_LANGPACK_USERNAME
        test $AUTOGRAPH_MAR_PASSWORD
        test $AUTOGRAPH_MAR_STAGE_PASSWORD
        test $AUTOGRAPH_MAR_STAGE_USERNAME
        test $AUTOGRAPH_MAR_USERNAME
        test $AUTOGRAPH_OMNIJA_PASSWORD
        test $AUTOGRAPH_OMNIJA_USERNAME
        test $AUTOGRAPH_WIDEVINE_PASSWORD
        test $AUTOGRAPH_WIDEVINE_USERNAME
        test $SIGNING_SERVER_PASSWORD
        test $SIGNING_SERVER_USERNAME
        ;;
      mobile)
        test $AUTOGRAPH_FENIX_PASSWORD
        test $AUTOGRAPH_FENIX_USERNAME
        test $AUTOGRAPH_FOCUS_PASSWORD
        test $AUTOGRAPH_FOCUS_USERNAME
        test $AUTOGRAPH_GPG_PASSWORD
        test $AUTOGRAPH_GPG_USERNAME
        test $AUTOGRAPH_REFERENCE_BROWSER_PASSWORD
        test $AUTOGRAPH_REFERENCE_BROWSER_USERNAME
        ;;
      application-services)
        test $AUTOGRAPH_GPG_PASSWORD
        test $AUTOGRAPH_GPG_USERNAME
        ;;
    esac
    ;;
  prod)
    case $COT_PRODUCT in
      firefox|thunderbird)
        test $AUTOGRAPH_FENNEC_NIGHTLY_PASSWORD
        test $AUTOGRAPH_FENNEC_NIGHTLY_USERNAME
        test $AUTOGRAPH_FENNEC_RELEASE_PASSWORD
        test $AUTOGRAPH_FENNEC_RELEASE_USERNAME
        test $AUTOGRAPH_GPG_PASSWORD
        test $AUTOGRAPH_GPG_USERNAME
        test $AUTOGRAPH_LANGPACK_PASSWORD
        test $AUTOGRAPH_LANGPACK_USERNAME
        test $AUTOGRAPH_MAR_NIGHTLY_PASSWORD
        test $AUTOGRAPH_MAR_NIGHTLY_USERNAME
        test $AUTOGRAPH_MAR_RELEASE_PASSWORD
        test $AUTOGRAPH_MAR_RELEASE_USERNAME
        test $AUTOGRAPH_OMNIJA_PASSWORD
        test $AUTOGRAPH_OMNIJA_USERNAME
        test $AUTOGRAPH_WIDEVINE_PASSWORD
        test $AUTOGRAPH_WIDEVINE_USERNAME
        test $SIGNING_SERVER_NIGHTLY_PASSWORD
        test $SIGNING_SERVER_NIGHTLY_USERNAME
        test $SIGNING_SERVER_RELEASE_PASSWORD
        test $SIGNING_SERVER_RELEASE_USERNAME
        ;;
      mobile)
        test $AUTOGRAPH_FENIX_BETA_PASSWORD
        test $AUTOGRAPH_FENIX_BETA_USERNAME
        test $AUTOGRAPH_FENIX_NIGHTLY_PASSWORD
        test $AUTOGRAPH_FENIX_NIGHTLY_USERNAME
        test $AUTOGRAPH_FENIX_PASSWORD
        test $AUTOGRAPH_FENIX_USERNAME
        test $AUTOGRAPH_FOCUS_PASSWORD
        test $AUTOGRAPH_FOCUS_USERNAME
        test $AUTOGRAPH_GPG_PASSWORD
        test $AUTOGRAPH_GPG_USERNAME
        test $AUTOGRAPH_REFERENCE_BROWSER_PASSWORD
        test $AUTOGRAPH_REFERENCE_BROWSER_USERNAME
        ;;
      application-services)
        test $AUTOGRAPH_GPG_USERNAME
        test $AUTOGRAPH_GPG_PASSWORD
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac


$CONFIG_LOADER $TEMPLATE_DIR/passwords.yml $PASSWORDS_PATH
