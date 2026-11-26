idf_component_register(
    SRCS "main.c" "board.c" "cam.c" "detect.c" "web.c" "net.c" "jpeg.c" "ui.c"
    INCLUDE_DIRS "."
    EMBED_TXTFILES "index.html"
    REQUIRES esp_driver_i2c esp_driver_jpeg esp_driver_usb_serial_jtag esp_http_server esp_netif
             esp_event esp_wifi esp_timer nvs_flash json vfs esp_app_format
             esp_driver_ppa esp_driver_ledc tab5_board)
