#!/system/bin/sh

check_device_compatibility() {
    compat_file="$AKHOME/device-compatibility.prop"
    for compat_key in model device android firmware release; do
        compat_count=$(grep -c "^${compat_key}=" "$compat_file")
        [ "$compat_count" = 1 ] || abort "Invalid device compatibility metadata"
    done
    compat_model=$(sed -n 's/^model=//p' "$compat_file")
    compat_device=$(sed -n 's/^device=//p' "$compat_file")
    compat_android=$(sed -n 's/^android=//p' "$compat_file")
    compat_firmware=$(sed -n 's/^firmware=//p' "$compat_file")
    compat_release=$(sed -n 's/^release=//p' "$compat_file")
    [ "$(getprop ro.product.model)" = "$compat_model" ] || abort "This kernel requires $compat_model"
    [ "$(getprop ro.product.device)" = "$compat_device" ] || abort "This kernel requires $compat_device"
    [ "$(getprop ro.build.version.release)" = "$compat_android" ] || abort "This kernel requires Android $compat_android"
    if [ "$BOOTMODE" = true ]; then
        [ "$(getprop ro.build.display.id)" = "$compat_firmware" ] || abort "Firmware does not match $compat_firmware"
        [ "$(uname -r)" = "$compat_release" ] || abort "Running kernel does not match the reference release"
    fi
    ui_print "Device compatibility: $compat_model / Android $compat_android"
}
