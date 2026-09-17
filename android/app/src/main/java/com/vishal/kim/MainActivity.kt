package com.vishal.kim

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.speech.RecognizerIntent
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity() {
    private val baseUrl = "https://app.vishalojha.me"
    private lateinit var web: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "kim-approval-watch", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<KimApprovalWorker>(15, TimeUnit.MINUTES).build()
        )
        setContentView(R.layout.activity_main)
        web = findViewById(R.id.kim_web)
        configureWebView()
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 5)
        }
        onBackPressedDispatcher.addCallback(this) {
            if (web.canGoBack()) web.goBack() else finish()
        }
        web.loadUrl("$baseUrl/?client=mobile")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        val settings = web.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.mediaPlaybackRequiresUserGesture = false
        settings.databaseEnabled = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        web.addJavascriptInterface(KimBridge(this), "KimNative")
        web.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest?) {
                request?.grant(request?.resources)
            }
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val target = request?.url ?: return false
                val isHosted = target.host == Uri.parse(baseUrl).host
                if (request.isForMainFrame && isHosted) return false
                startActivity(Intent(Intent.ACTION_VIEW, target))
                return true
            }
        }
    }

    fun startVoiceInput() {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(android.Manifest.permission.RECORD_AUDIO), 5)
            return
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
        }
        runCatching { startActivityForResult(intent, 7) }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 7 && resultCode == RESULT_OK) {
            val transcript = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull().orEmpty()
            val safe = transcript.replace("\\", "\\\\").replace("'", "\\'")
            web.evaluateJavascript("window.__kimSetTranscript && window.__kimSetTranscript('$safe')", null)
        }
    }

    inner class KimBridge(private val host: MainActivity) {
        @JavascriptInterface fun getPin(): String = KimPrefs.pin(host, KimPrefs.activeUser(host))
        @JavascriptInterface fun getUser(): String = KimPrefs.activeUser(host)
        @JavascriptInterface fun setPin(pin: String, user: String) { KimPrefs.savePin(host, user, pin) }
        @JavascriptInterface fun mic() = host.startVoiceInput()
        @JavascriptInterface fun download(url: String, jobId: String) {
            val user = KimPrefs.activeUser(host)
            val pin = KimPrefs.pin(host, user)
            val request = DownloadManager.Request(Uri.parse(url))
                .setTitle("Kim music")
                .setDescription("Downloading generated music")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalFilesDir(host, android.os.Environment.DIRECTORY_MUSIC, "$jobId.mp3")
                .addRequestHeader("X-Kim-Pin", pin)
                .addRequestHeader("X-Kim-User", user)
            host.getSystemService(DownloadManager::class.java).enqueue(request)
        }
    }
}