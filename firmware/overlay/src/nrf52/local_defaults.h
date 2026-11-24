#pragma once
#include "meshcom_settings.h"

bool applyLocalDefaults(s_meshcom_settings &settings);
void localSettingsSetSource(const char *source);
const char *localSettingsSource();
const char *localDefaultsProfile();
