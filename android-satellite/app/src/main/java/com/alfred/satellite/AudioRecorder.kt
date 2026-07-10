package com.alfred.satellite

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import kotlin.math.sqrt

/**
 * Captures one spoken turn from the mic as a mono 16 kHz 16-bit WAV, ending on
 * an energy-based voice-activity gate — the native analogue of voice.py's
 * record_command/calibrate_threshold on the CLI satellite.
 *
 * Caller MUST hold RECORD_AUDIO before invoking recordTurn().
 */
class AudioRecorder {

    companion object {
        const val SAMPLE_RATE = 16_000
        private const val FRAME = 512                 // ~32 ms per read
        private const val CALIBRATION_MS = 300        // sample the room first
        private const val SILENCE_HANG_MS = 800       // trailing silence ends the turn
        private const val MAX_SPEECH_MS = 10_000      // hard cap
        private const val START_TIMEOUT_MS = 6_000    // give up if no speech begins
        private const val MIN_SPEECH_MS = 300         // ignore a lone click
        private const val THRESHOLD_FACTOR = 3.5      // speech = this * noise floor
        private const val MIN_THRESHOLD = 500.0       // floor for a very quiet room
    }

    @Volatile private var cancelled = false

    fun cancel() { cancelled = true }

    /** Blocking. Returns WAV bytes, or null if nothing intelligible was heard. */
    @SuppressLint("MissingPermission")
    fun recordTurn(): ByteArray? {
        cancelled = false
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuf, FRAME * 4 * 2),
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            return null
        }

        val pcm = ByteArrayOutputStream()
        val buf = ShortArray(FRAME)
        try {
            record.startRecording()

            // 1) Calibrate the noise floor.
            var floorSum = 0.0
            var floorCount = 0
            val calFrames = (CALIBRATION_MS.toDouble() / 1000 * SAMPLE_RATE / FRAME).toInt()
            repeat(calFrames) {
                val n = record.read(buf, 0, FRAME)
                if (n > 0) { floorSum += rms(buf, n); floorCount++ }
            }
            val floor = if (floorCount > 0) floorSum / floorCount else 0.0
            val threshold = maxOf(floor * THRESHOLD_FACTOR, MIN_THRESHOLD)

            // 2) Wait for speech, then record until trailing silence.
            var speechStarted = false
            var elapsedMs = 0.0
            var speechMs = 0.0
            var silenceMs = 0.0
            val frameMs = FRAME.toDouble() / SAMPLE_RATE * 1000

            while (!cancelled) {
                val n = record.read(buf, 0, FRAME)
                if (n <= 0) continue
                elapsedMs += frameMs
                val level = rms(buf, n)
                val voiced = level >= threshold

                if (!speechStarted) {
                    if (voiced) {
                        speechStarted = true
                        appendFrame(pcm, buf, n)
                        speechMs += frameMs
                    } else if (elapsedMs >= START_TIMEOUT_MS) {
                        break  // nobody spoke
                    }
                } else {
                    appendFrame(pcm, buf, n)
                    speechMs += frameMs
                    silenceMs = if (voiced) 0.0 else silenceMs + frameMs
                    if (silenceMs >= SILENCE_HANG_MS && speechMs >= MIN_SPEECH_MS) break
                    if (speechMs >= MAX_SPEECH_MS) break
                }
            }
        } finally {
            runCatching { record.stop() }
            record.release()
        }

        val bytes = pcm.toByteArray()
        if (cancelled || bytes.isEmpty()) return null
        return wrapWav(bytes)
    }

    private fun rms(buf: ShortArray, n: Int): Double {
        var sum = 0.0
        for (i in 0 until n) { val s = buf[i].toDouble(); sum += s * s }
        return sqrt(sum / n)
    }

    private fun appendFrame(out: ByteArrayOutputStream, buf: ShortArray, n: Int) {
        for (i in 0 until n) {
            val v = buf[i].toInt()
            out.write(v and 0xFF)
            out.write((v shr 8) and 0xFF)
        }
    }

    /** Prepend a 44-byte PCM WAV header (mono, 16-bit, SAMPLE_RATE). */
    private fun wrapWav(pcm: ByteArray): ByteArray {
        val channels = 1
        val bits = 16
        val byteRate = SAMPLE_RATE * channels * bits / 8
        val blockAlign = channels * bits / 8
        val dataLen = pcm.size
        val out = ByteArrayOutputStream(44 + dataLen)

        fun str(s: String) = out.write(s.toByteArray(Charsets.US_ASCII))
        fun le32(v: Int) {
            out.write(v and 0xFF); out.write((v shr 8) and 0xFF)
            out.write((v shr 16) and 0xFF); out.write((v shr 24) and 0xFF)
        }
        fun le16(v: Int) { out.write(v and 0xFF); out.write((v shr 8) and 0xFF) }

        str("RIFF"); le32(36 + dataLen); str("WAVE")
        str("fmt "); le32(16); le16(1); le16(channels)
        le32(SAMPLE_RATE); le32(byteRate); le16(blockAlign); le16(bits)
        str("data"); le32(dataLen)
        out.write(pcm)
        return out.toByteArray()
    }
}
