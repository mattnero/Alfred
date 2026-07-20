package com.alfred.satellite

import android.content.Context
import ai.picovoice.porcupine.Porcupine
import ai.picovoice.porcupine.PorcupineManager
import ai.picovoice.porcupine.PorcupineManagerCallback

/**
 * Wraps Porcupine on-device wake-word detection. Ships on the built-in "jarvis"
 * keyword (free, no training). To use a custom "Alfred" wake word, train it in
 * the Picovoice Console, drop Alfred.ppn into app/src/main/assets/, and swap
 * setKeyword(...) for setKeywordPath("Alfred.ppn").
 *
 * Porcupine owns the mic while running, so the service must stop() it before
 * recording a command and start() it again afterwards.
 */
class WakeListener(
    private val context: Context,
    private val accessKey: String,
    private val onWake: () -> Unit,
) {
    private var manager: PorcupineManager? = null
    private var released = false

    /** Builds on first call. Throws PorcupineException on a bad key / activation. */
    @Synchronized
    fun start() {
        if (released) return
        if (manager == null) {
            manager = PorcupineManager.Builder()
                .setAccessKey(accessKey)
                .setKeyword(Porcupine.BuiltInKeyword.JARVIS)
                .setSensitivity(0.6f)
                .build(context, PorcupineManagerCallback { _ -> onWake() })
        }
        manager?.start()
    }

    @Synchronized
    fun stop() {
        runCatching { manager?.stop() }
    }

    @Synchronized
    fun release() {
        released = true
        manager?.let {
            runCatching { it.stop() }
            runCatching { it.delete() }
        }
        manager = null
    }
}
