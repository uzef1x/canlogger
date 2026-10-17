// WiFi (phone hotspot) + HTTP batch uploader. Runs on its own task, core 0.
#pragma once
#include <stdint.h>
void uplink_start();
bool uplink_connected();
uint32_t uplink_sent();   // records acknowledged by the server
