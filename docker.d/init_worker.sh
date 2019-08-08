#!/bin/bash
set -e

# 
# Check that all required variables exist
#
test $CONFIG_DIR
test $COT_PRODUCT
test $DATADOG_HOST
test $DATADOG_PORT
test $GPG_PUBKEY
test $PROJECT_NAME
test $PUBLIC_IP

# TODO: where do we get this script??
#export DMG_PATH=...
#export HFSPLUS_PATH=...
#export ZIPALIGN_PATH=...

export SIGNTOOL_PATH="/app/signtool"
export SSL_CERT_PATH="$(dirname $0)/../signingscript/data/host.cert"
export GPG_PUBKEY_PATH=$CONFIG_DIR/gpg_pubkey
export WIDEVINE_CERT_PATH=$CONFIG_DIR/widevine.crt

echo $GPG_PUBKEY | base64 -d > $GPG_PUBKEY_PATH

case $COT_PRODUCT in
  firefox)
    test $WIDEVINE_CERT
    echo $WIDEVINE_CERT | base64 -d > $WIDEVINE_CERT_PATH
    export TASKCLUSTER_SCOPE_PREFIX="project:releng:${PROJECT_NAME}script:"
    ;;
  thunderbird)
    export TASKCLUSTER_SCOPE_PREFIX="project:comm:thunderbird:releng:${PROJECT_NAME}script:"
    ;;
  *)
    exit 1
    ;;
esac
