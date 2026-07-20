package com.alfred.satellite

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack

/**
 * Plays back the brain's TTS. Reply sentences arrive as separate 24 kHz WAV
 * blobs; a single streaming AudioTrack is fed each sentence's PCM in order so
 * playback is continuous. start() once per turn, writeWav() per sentence,
 * finish() to drain, abort() to cut off.
 */
class AudioPlayer {

    companion object { const val SAMPLE_RATE = 24_000 }

    private var track: AudioTrack? = null

    fun start() {
        if (track != null) return
        val minBuf = AudioTrack.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT,
        )
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setBufferSizeInBytes(maxOf(minBuf, SAMPLE_RATE))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        track?.play()
    }

    /** Blocking write of one sentence's WAV (header stripped) to the track. */
    fun writeWav(wav: ByteArray) {
        val t = track ?: return
        val offset = pcmOffset(wav)
        var pos = offset
        while (pos < wav.size) {
            val written = t.write(wav, pos, wav.size - pos)
            if (written <= 0) break
            pos += written
        }
    }

    /** Let queued audio play out, then release. */
    fun finish() {
        val t = track ?: return
        runCatching { t.stop() }   // MODE_STREAM: drains pending buffer
        runCatching { t.release() }
        track = null
    }

    /** Cut playback immediately (e.g. on stop). */
    fun abort() {
        val t = track ?: return
        runCatching { t.pause(); t.flush(); t.stop() }
        runCatching { t.release() }
        track = null
    }

    /** Locate the PCM data: find the "data" subchunk, else assume a 44-byte header. */
    private fun pcmOffset(wav: ByteArray): Int {
        var i = 12  // skip "RIFF"<size>"WAVE"
        while (i + 8 <= wav.size) {
            val id = String(wav, i, 4, Charsets.US_ASCII)
            val size = (wav[i + 4].toInt() and 0xFF) or
                ((wav[i + 5].toInt() and 0xFF) shl 8) or
                ((wav[i + 6].toInt() and 0xFF) shl 16) or
                ((wav[i + 7].toInt() and 0xFF) shl 24)
            if (id == "data") return i + 8
            if (size < 0) break
            i += 8 + size + (size and 1)  // chunks are word-aligned
        }
        return minOf(44, wav.size)
    }
}
