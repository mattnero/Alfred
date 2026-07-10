package com.alfred.satellite

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Foreground (microphone) service that owns the always-listening loop:
 *
 *   IDLE --wake--> LISTENING --end-of-speech--> THINKING --> SPEAKING --> IDLE
 *
 * Porcupine listens in IDLE; on the wake word it is stopped (to free the mic),
 * one command is recorded, POSTed to the brain, and the streamed reply is spoken
 * sentence-by-sentence. Then Porcupine restarts. One turn at a time.
 */
class SatelliteService : Service() {

    companion object {
        const val ACTION_STOP = "com.alfred.satellite.STOP"
        private const val CHANNEL_ID = "alfred_satellite"
        private const val NOTIF_ID = 1
    }

    private val turns = Executors.newSingleThreadExecutor()
    private val busy = AtomicBoolean(false)

    private lateinit var wake: WakeListener
    private lateinit var brain: BrainClient
    private val recorder = AudioRecorder()
    private val player = AudioPlayer()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }

        startForegroundNotification()

        val prefs = Prefs(this)
        if (prefs.serverUrl.isBlank() || prefs.accessKey.isBlank()) {
            SatelliteState.update(State.ERROR, "Set the brain URL and AccessKey first")
            stopSelf()
            return START_NOT_STICKY
        }

        brain = BrainClient(prefs.serverUrl)
        wake = WakeListener(applicationContext, prefs.accessKey) { onWake() }

        // Probe the brain for the status line (best-effort).
        turns.execute {
            runCatching { brain.health() }
                .onSuccess { h ->
                    val caps = buildList {
                        if (h.homeControl) add("home control")
                        if (h.calendar) add("calendar")
                    }
                    SatelliteState.setHealth(
                        "Connected · ${h.model}" + if (caps.isEmpty()) " · chat only" else " · ${caps.joinToString(", ")}",
                    )
                }
                .onFailure { SatelliteState.setHealth("Cannot reach the brain") }
        }

        try {
            wake.start()
            SatelliteState.update(State.IDLE, "Say “Jarvis”")
        } catch (e: Exception) {
            SatelliteState.update(State.ERROR, e.message ?: "wake-word init failed")
            stopSelf()
            return START_NOT_STICKY
        }

        return START_STICKY
    }

    private fun onWake() {
        if (!busy.compareAndSet(false, true)) return
        turns.execute {
            try {
                runTurn()
            } finally {
                busy.set(false)
            }
        }
    }

    private fun runTurn() {
        var lastError: String? = null
        try {
            SatelliteState.update(State.LISTENING, "Listening…")
            wake.stop()  // free the mic for AudioRecord

            val wav = recorder.recordTurn()
            if (wav == null) {
                return  // nothing heard; finally restarts the wake word
            }

            SatelliteState.update(State.THINKING, "Thinking…")
            var speaking = false
            brain.chatAudio(
                wav = wav,
                onTranscript = { t ->
                    if (t.isNotBlank()) SatelliteState.update(State.THINKING, "“$t”")
                },
                onSentence = { _, audio ->
                    if (audio != null) {
                        if (!speaking) {
                            player.start()
                            speaking = true
                            SatelliteState.update(State.SPEAKING, "Speaking…")
                        }
                        player.writeWav(audio)
                    }
                },
                onError = { e -> lastError = e },
            )
            if (speaking) player.finish()
        } catch (e: Exception) {
            lastError = e.message ?: "error"
        } finally {
            runCatching { wake.start() }
            if (lastError != null) {
                SatelliteState.update(State.IDLE, "Last error: $lastError")
            } else {
                SatelliteState.update(State.IDLE, "Say “Jarvis”")
            }
        }
    }

    override fun onDestroy() {
        runCatching { player.abort() }
        runCatching { recorder.cancel() }
        runCatching { wake.release() }
        turns.shutdownNow()
        SatelliteState.update(State.STOPPED, "")
        super.onDestroy()
    }

    private fun startForegroundNotification() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notif_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            )
            nm.createNotificationChannel(channel)
        }
        val notif: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.notif_title))
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIF_ID, notif)
        }
    }
}
