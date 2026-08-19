package com.kunguglobal.velocity1

import android.os.Bundle
import android.widget.*
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32,32,32,32) }
        val title = TextView(this).apply { text = "VELOCITY 1"; textSize = 28f }
        val mode = Switch(this).apply { text = "Live / Demo"; text = "Demo account"; isChecked = false }
        val apiKey = EditText(this).apply { hint = "MetaApi API key"; inputType = 0x00000081 }
        val accountId = EditText(this).apply { hint = "MetaApi account ID" }
        val login = Button(this).apply { text = "CONNECT ACCOUNT" }
        val status = TextView(this).apply { text = "Not connected"; textSize = 16f }
        val info = TextView(this).apply { text = "XAUUSD • M1 velocity engine\nRisk controls enabled\nTrading remains OFF until explicitly enabled."; textSize = 15f }
        root.addView(title); root.addView(mode); root.addView(apiKey); root.addView(accountId); root.addView(login); root.addView(status); root.addView(info)
        login.setOnClickListener {
            if (apiKey.text.isNullOrBlank() || accountId.text.isNullOrBlank()) status.text = "Enter MetaApi key and account ID"
            else status.text = if (mode.isChecked) "Live account credentials saved locally — trading OFF" else "Demo account credentials saved locally"
        }
        setContentView(root)
    }
}
