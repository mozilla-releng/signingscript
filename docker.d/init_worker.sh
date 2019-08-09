#!/bin/bash
set -e

#
# Check that all required variables exist
#
test $CONFIG_DIR
test $COT_PRODUCT
test $GPG_PUBKEY
test $PROJECT_NAME
test $PUBLIC_IP

export DMG_PATH=/app/files/dmg
export HFSPLUS_PATH=/app/files/hfsplus
export ZIPALIGN_PATH=/usr/bin/zipalign

export PASSWORDS_PATH=$CONFIG_DIR/passwords.json
export SIGNTOOL_PATH="/app/bin/signtool"
export SSL_CERT_PATH="/app/signingscript/data/host.cert"
export GPG_PUBKEY_PATH=$CONFIG_DIR/gpg_pubkey
export WIDEVINE_CERT_PATH=$CONFIG_DIR/widevine.crt

echo $GPG_PUBKEY | base64 -d > $GPG_PUBKEY_PATH

case $COT_PRODUCT in
  firefox)
    test $WIDEVINE_CERT

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
