package com.alfred.satellite

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.alfred.satellite.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: Prefs

    private val permissionRequest =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
            if (result[Manifest.permission.RECORD_AUDIO] != false &&
                ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED
            ) {
                launchService()
            } else {
                binding.status.text = getString(R.string.status_idle)
                binding.health.text = "Microphone permission is required to listen."
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = Prefs(this)

        binding.serverUrl.setText(prefs.serverUrl)
        binding.accessKey.setText(prefs.accessKey)

        binding.toggle.setOnClickListener {
            if (SatelliteState.state == State.STOPPED) start() else stop()
        }
    }

    override fun onResume() {
        super.onResume()
        SatelliteState.listener = { state, detail, health -> render(state, detail, health) }
        render(SatelliteState.state, SatelliteState.detail, SatelliteState.health)
    }

    override fun onPause() {
        super.onPause()
        SatelliteState.listener = null
    }

    private fun start() {
        prefs.serverUrl = binding.serverUrl.text.toString()
        prefs.accessKey = binding.accessKey.text.toString()
        if (prefs.serverUrl.isBlank() || prefs.accessKey.isBlank()) {
            binding.health.text = "Enter both the brain URL and your Picovoice AccessKey."
            return
        }

        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        val missing = needed.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) launchService() else permissionRequest.launch(missing.toTypedArray())
    }

    private fun launchService() {
        val intent = Intent(this, SatelliteService::class.java)
        ContextCompat.startForegroundService(this, intent)
    }

    private fun stop() {
        stopService(Intent(this, SatelliteService::class.java))
    }

    private fun render(state: State, detail: String, health: String) {
        binding.toggle.text = getString(if (state == State.STOPPED) R.string.start else R.string.stop)
        val label = when (state) {
            State.STOPPED -> getString(R.string.status_idle)
            State.IDLE -> "Idle — ${detail.ifBlank { "listening for the wake word" }}"
            State.LISTENING -> "Listening…"
            State.THINKING -> detail.ifBlank { "Thinking…" }
            State.SPEAKING -> "Speaking…"
            State.ERROR -> "Error: $detail"
        }
        binding.status.text = label
        binding.health.text = health
    }
}
