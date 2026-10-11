#!/bin/sh
# HELP: Brattoi
# ICON: brattoi
# GRID: Brattoi

. /opt/muos/script/var/func.sh

APP_NAME="Brattoi"
APP_SUBPATH="application/$APP_NAME"
APP_DIRECTORY="$MUOS_SHARE_DIR/$APP_SUBPATH"
[ -d "$APP_DIRECTORY" ] || APP_DIRECTORY="$MUOS_STORE_DIR/$APP_SUBPATH"

TERM_CFG_DIR="/opt/muos/share/info/config/Terminal"
mkdir -p "$TERM_CFG_DIR"

SETUP_STAGE_OVERLAY
SETUP_APP "muxterm" ""

# SETUP_APP applies the app governor with cp, which cannot write to sysfs, so apply it here.
# Fixed upstream (internal#769); kept for older builds, harmless on fixed ones.
[ -s "$MUOS_RUN_DIR/governor" ] && cat "$MUOS_RUN_DIR/governor" >"$(GET_VAR "device" "cpu/governor")"

cd "$APP_DIRECTORY/.brattoi" || exit 1
/opt/muos/frontend/muxterm --gl -c "$TERM_CFG_DIR/$APP_NAME.conf" -- python3 chat.py
