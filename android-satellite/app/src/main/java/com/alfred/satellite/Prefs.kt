package com.alfred.satellite

import android.content.Context

/** Thin wrapper over SharedPreferences for the two settings the satellite needs:
 *  the brain's LAN URL and the Picovoice AccessKey. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("alfred", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("serverUrl", "")!!.trim()
        set(value) = sp.edit().putString("serverUrl", value.trim()).apply()

    var accessKey: String
        get() = sp.getString("accessKey", "")!!.trim()
        set(value) = sp.edit().putString("accessKey", value.trim()).apply()
}
