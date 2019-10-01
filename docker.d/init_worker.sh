#!/bin/bash
set -e

#
# Check that all required variables exist
#
check_var CONFIG_DIR
check_var CONFIG_LOADER
check_var COT_PRODUCT
check_var GPG_PUBKEY
check_var PROJECT_NAME
check_var PUBLIC_IP
check_var TEMPLATE_DIR

export DMG_PATH=/app/files/dmg
export HFSPLUS_PATH=/app/files/hfsplus
export ZIPALIGN_PATH=/usr/bin/zipalign

export PASSWORDS_PATH=$CONFIG_DIR/passwords.json
export SIGNTOOL_PATH="/app/bin/signtool"
export SSL_CERT_PATH="/app/src/signingscript/data/host.cert"
export GPG_PUBKEY_PATH=$CONFIG_DIR/gpg_pubkey
export WIDEVINE_CERT_PATH=$CONFIG_DIR/widevine.crt
export AUTHENTICODE_TIMESTAMP_STYLE=null
export AUTHENTICODE_CERT_PATH=/app/src/signingscript/data/authenticode_dep.crt
export AUTHENTICODE_CROSS_CERT_PATH=/app/src/signingscript/data/authenticode_stub.crt
if [ "$ENV" == "prod" ]; then
  export AUTHENTICODE_TIMESTAMP_STYLE=old
  export AUTHENTICODE_CERT_PATH=/app/src/signingscript/data/authenticode_prod.crt
fi

echo $GPG_PUBKEY | base64 -d > $GPG_PUBKEY_PATH

case $COT_PRODUCT in
  firefox)
    check_var WIDEVINE_CERT

    echo $WIDEVINE_CERT | base64 -d > $WIDEVINE_CERT_PATH
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
  dev)
    check_var AUTOGRAPH_AUTHENTICODE_PASSWORD
    check_var AUTOGRAPH_AUTHENTICODE_USERNAME
    check_var AUTHENTICODE_CERT_PATH
    check_var AUTHENTICODE_CROSS_CERT_PATH
    check_var AUTHENTICODE_TIMESTAMP_STYLE
    check_var AUTOGRAPH_FENNEC_PASSWORD
    check_var AUTOGRAPH_FENNEC_USERNAME
    check_var AUTOGRAPH_GPG_PASSWORD
    check_var AUTOGRAPH_GPG_USERNAME
    check_var AUTOGRAPH_LANGPACK_PASSWORD
    check_var AUTOGRAPH_LANGPACK_USERNAME
    check_var AUTOGRAPH_MAR_PASSWORD
    check_var AUTOGRAPH_MAR_STAGE_PASSWORD
    check_var AUTOGRAPH_MAR_STAGE_USERNAME
    check_var AUTOGRAPH_MAR_USERNAME
    check_var AUTOGRAPH_OMNIJA_PASSWORD
    check_var AUTOGRAPH_OMNIJA_USERNAME
    check_var AUTOGRAPH_WIDEVINE_PASSWORD
    check_var AUTOGRAPH_WIDEVINE_USERNAME
    ;;
  fake-prod)
    case $COT_PRODUCT in
      firefox|thunderbird)
        check_var AUTOGRAPH_AUTHENTICODE_PASSWORD
        check_var AUTOGRAPH_AUTHENTICODE_USERNAME
        check_var AUTHENTICODE_CERT_PATH
        check_var AUTHENTICODE_CROSS_CERT_PATH
        check_var AUTHENTICODE_TIMESTAMP_STYLE
        check_var AUTOGRAPH_FENNEC_PASSWORD
        check_var AUTOGRAPH_FENNEC_USERNAME
        check_var AUTOGRAPH_GPG_PASSWORD
        check_var AUTOGRAPH_GPG_USERNAME
        check_var AUTOGRAPH_LANGPACK_PASSWORD
        check_var AUTOGRAPH_LANGPACK_USERNAME
        check_var AUTOGRAPH_MAR_PASSWORD
        check_var AUTOGRAPH_MAR_STAGE_PASSWORD
        check_var AUTOGRAPH_MAR_STAGE_USERNAME
        check_var AUTOGRAPH_MAR_USERNAME
        check_var AUTOGRAPH_OMNIJA_PASSWORD
        check_var AUTOGRAPH_OMNIJA_USERNAME
        check_var AUTOGRAPH_WIDEVINE_PASSWORD
        check_var AUTOGRAPH_WIDEVINE_USERNAME
        ;;
      mobile)
        check_var AUTOGRAPH_FENIX_PASSWORD
        check_var AUTOGRAPH_FENIX_USERNAME
        check_var AUTOGRAPH_FOCUS_PASSWORD
        check_var AUTOGRAPH_FOCUS_USERNAME
        check_var AUTOGRAPH_GPG_PASSWORD
        check_var AUTOGRAPH_GPG_USERNAME
        check_var AUTOGRAPH_REFERENCE_BROWSER_PASSWORD
        check_var AUTOGRAPH_REFERENCE_BROWSER_USERNAME
        ;;
      application-services)
        check_var AUTOGRAPH_GPG_PASSWORD
        check_var AUTOGRAPH_GPG_USERNAME
        ;;
    esac
    ;;
  prod)
    case $COT_PRODUCT in
      firefox|thunderbird)
        check_var AUTOGRAPH_AUTHENTICODE_PASSWORD
        check_var AUTOGRAPH_AUTHENTICODE_USERNAME
        check_var AUTHENTICODE_CERT_PATH
        check_var AUTHENTICODE_CROSS_CERT_PATH
        check_var AUTHENTICODE_TIMESTAMP_STYLE
        check_var AUTOGRAPH_FENNEC_NIGHTLY_PASSWORD
        check_var AUTOGRAPH_FENNEC_NIGHTLY_USERNAME
        check_var AUTOGRAPH_FENNEC_RELEASE_PASSWORD
        check_var AUTOGRAPH_FENNEC_RELEASE_USERNAME
        check_var AUTOGRAPH_GPG_PASSWORD
        check_var AUTOGRAPH_GPG_PASSWORD
        check_var AUTOGRAPH_LANGPACK_PASSWORD
        check_var AUTOGRAPH_LANGPACK_USERNAME
        check_var AUTOGRAPH_MAR_NIGHTLY_PASSWORD
        check_var AUTOGRAPH_MAR_NIGHTLY_USERNAME
        check_var AUTOGRAPH_MAR_RELEASE_PASSWORD
        check_var AUTOGRAPH_MAR_RELEASE_USERNAME
        check_var AUTOGRAPH_OMNIJA_PASSWORD
        check_var AUTOGRAPH_OMNIJA_USERNAME
        check_var AUTOGRAPH_WIDEVINE_PASSWORD
        check_var AUTOGRAPH_WIDEVINE_USERNAME
        ;;
      mobile)
        check_var AUTOGRAPH_FENIX_BETA_PASSWORD
        check_var AUTOGRAPH_FENIX_BETA_USERNAME
        check_var AUTOGRAPH_FENIX_NIGHTLY_PASSWORD
        check_var AUTOGRAPH_FENIX_NIGHTLY_USERNAME
        check_var AUTOGRAPH_FENIX_PASSWORD
        check_var AUTOGRAPH_FENIX_USERNAME
        check_var AUTOGRAPH_FOCUS_PASSWORD
        check_var AUTOGRAPH_FOCUS_USERNAME
        check_var AUTOGRAPH_GPG_PASSWORD
        check_var AUTOGRAPH_GPG_USERNAME
        check_var AUTOGRAPH_REFERENCE_BROWSER_PASSWORD
        check_var AUTOGRAPH_REFERENCE_BROWSER_USERNAME
        ;;
      application-services)
        check_var AUTOGRAPH_GPG_USERNAME
        check_var AUTOGRAPH_GPG_PASSWORD
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac


$CONFIG_LOADER $TEMPLATE_DIR/passwords.yml $PASSWORDS_PATH
