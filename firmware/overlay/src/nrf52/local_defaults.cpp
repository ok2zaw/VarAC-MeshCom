#ifdef NRF52_SERIES
#include "local_defaults.h"
#include "loop_functions.h"
#if defined(MC_LOCAL_DEFAULTS) && MC_LOCAL_DEFAULTS
#include "local_profile.h"
#endif

namespace { const char *settings_source = "unknown"; }
void localSettingsSetSource(const char *source) { settings_source = source; }
const char *localSettingsSource() { return settings_source; }

const char *localDefaultsProfile() {
#if defined(MC_LOCAL_DEFAULTS) && MC_LOCAL_DEFAULTS
    return LocalProfileId;
#else
    return "upstream";
#endif
}

bool applyLocalDefaults(s_meshcom_settings &settings) {
#if defined(MC_LOCAL_DEFAULTS) && MC_LOCAL_DEFAULTS
    applyLocalProfile(settings);
    snprintf(settings.node_short, sizeof(settings.node_short), "%s", convertCallToShort(settings.node_call).c_str());
    settings_source = "profile";
    Serial.printf("[PROFILE];applied;id;%s\n", LocalProfileId);
    return true;
#else
    (void)settings;
    return false;
#endif
}
#endif
