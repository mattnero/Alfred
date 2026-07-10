package com.alfred.satellite

import android.util.Base64
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Talks to the Alfred brain over the same HTTP seam the CLI satellite uses.
 *
 *  - GET  /health      -> model + capability flags for the status line.
 *  - POST /chat/audio  -> body is a mono 16 kHz WAV; the reply streams back as
 *    newline-delimited JSON: a leading {"transcript"}, then {"sentence","audio"}
 *    per sentence (audio = base64 24 kHz WAV), then an optional {"display_url"}.
 */
class BrainClient(baseUrl: String) {

    /** Normalised base URL with no trailing slash. */
    private val base: String = baseUrl.trim().trimEnd('/')

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // A turn = STT + LLM + TTS on the brain; give it room but not forever.
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    data class Health(val model: String, val homeControl: Boolean, val calendar: Boolean)

    /** Blocking. Throws IOException on failure. */
    fun health(): Health {
        val req = Request.Builder().url("$base/health").get().build()
        client.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw IOException("health HTTP ${resp.code}")
            val j = JSONObject(body)
            return Health(
                model = j.optString("model", "unknown"),
                homeControl = j.optBoolean("home_control", false),
                calendar = j.optBoolean("calendar", false),
            )
        }
    }

    /**
     * POST a WAV and dispatch the streamed reply. Blocking — call from a worker
     * thread. Callbacks fire on that same thread as lines arrive.
     */
    fun chatAudio(
        wav: ByteArray,
        onTranscript: (String) -> Unit,
        onSentence: (text: String, audio: ByteArray?) -> Unit,
        onError: (String) -> Unit,
    ) {
        val req = Request.Builder()
            .url("$base/chat/audio")
            .post(wav.toRequestBody("audio/wav".toMediaType()))
            .build()

        client.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) {
                val msg = resp.body?.string()?.let {
                    runCatching { JSONObject(it).optString("error") }.getOrNull()
                }?.takeIf { it.isNotBlank() } ?: "HTTP ${resp.code}"
                onError(msg)
                return
            }
            val source = resp.body?.source() ?: run { onError("empty response"); return }
            while (true) {
                val line = source.readUtf8Line() ?: break
                if (line.isBlank()) continue
                val obj = runCatching { JSONObject(line) }.getOrNull() ?: continue
                when {
                    obj.has("transcript") -> onTranscript(obj.getString("transcript"))
                    obj.has("sentence") -> {
                        val audio = obj.optString("audio", "")
                            .takeIf { it.isNotEmpty() }
                            ?.let { Base64.decode(it, Base64.DEFAULT) }
                        onSentence(obj.getString("sentence"), audio)
                    }
                    obj.has("error") -> onError(obj.getString("error"))
                    // {"display_url"} intentionally ignored in v1 (no projector yet).
                }
            }
        }
    }
}
