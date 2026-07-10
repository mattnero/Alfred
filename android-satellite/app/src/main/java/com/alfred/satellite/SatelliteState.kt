package com.alfred.satellite

import android.os.Handler
import android.os.Looper

enum class State { STOPPED, IDLE, LISTENING, THINKING, SPEAKING, ERROR }

/**
 * Tiny in-process state bus so MainActivity can render what SatelliteService is
 * doing without pulling in LiveData/lifecycle. Updates are delivered on the main
 * thread. Mirrors the Listening/Thinking/Speaking states status.py drives on the
 * other satellites.
 */
object SatelliteState {
    private val main = Handler(Looper.getMainLooper())

    @Volatile var state: State = State.STOPPED
        private set
    @Volatile var detail: String = ""
        private set
    @Volatile var health: String = ""
        private set

    /** Set by MainActivity while it is visible. */
    @Volatile var listener: ((State, String, String) -> Unit)? = null

    fun update(s: State, d: String = detail) {
        state = s
        detail = d
        emit()
    }

    fun setHealth(text: String) {
        health = text
        emit()
    }

    private fun emit() {
        val l = listener ?: return
        main.post { l(state, detail, health) }
    }
}
