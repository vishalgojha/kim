package com.vishal.kim

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.app.DownloadManager
import android.net.Uri
import android.provider.Settings
import android.os.Bundle
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

private val KimBackground = Color(0xFF08090B)
private val KimPanel = Color(0xFF15171C)
private val KimBorder = Color(0xFF2A2E36)
private val KimMuted = Color(0xFF9299A8)

private data class ChatMessage(val user: Boolean, val text: String, val attachmentName: String? = null, val musicJobId: String? = null)

class MainActivity : ComponentActivity() {
    private val baseUrl = "https://app.vishalojha.me"
    private val executor = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WorkManager.getInstance(this).enqueueUniquePeriodicWork(
            "kim-approval-watch", ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<KimApprovalWorker>(15, TimeUnit.MINUTES).build()
        )
        setContent { KimTheme { KimApp() } }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    private fun KimApp() {
        val context = this@MainActivity
        val scope = rememberCoroutineScope()
        val drawer = rememberDrawerState(androidx.compose.material3.DrawerValue.Closed)
        var activeUser by remember { mutableStateOf(KimPrefs.activeUser(context)) }
        var conversationId by remember { mutableStateOf(KimPrefs.currentChatId(context, activeUser)) }
        var historyRefresh by remember { mutableStateOf(0) }
        val chatHistory = remember(historyRefresh, activeUser) { KimPrefs.history(context, activeUser) }
        val messages = remember(activeUser, conversationId) {
            mutableStateListOf<ChatMessage>().also { list -> KimPrefs.messages(context, activeUser, conversationId).forEach { list.add(ChatMessage(it.first, it.second)) } }
        }
        var input by remember { mutableStateOf("") }
        var thinking by remember { mutableStateOf(false) }
        var voiceActive by remember { mutableStateOf(false) }
        var profileMenu by remember { mutableStateOf(false) }
        var actionMenu by remember { mutableStateOf(false) }
        var signIn by remember { mutableStateOf(KimPrefs.pin(context, activeUser).isBlank()) }
        var pin by remember { mutableStateOf(KimPrefs.pin(context, activeUser)) }
        var attachmentName by remember { mutableStateOf<String?>(null) }
        var attachmentText by remember { mutableStateOf<String?>(null) }
        val listState = rememberLazyListState()
        val filePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri == null) return@rememberLauncherForActivityResult
            val name = runCatching {
                context.contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                    if (cursor.moveToFirst()) cursor.getString(0) else null
                }
            }.getOrNull() ?: "attachment"
            val text = runCatching {
                context.contentResolver.openInputStream(uri)?.use { stream ->
                    stream.readBytes().take(200_000).toByteArray().toString(Charsets.UTF_8)
                }
            }.getOrNull().orEmpty()
            attachmentName = name
            attachmentText = text.ifBlank { "[Attached file: $name; text could not be extracted on this device]" }
        }

        fun send() {
            val text = input.trim()
            if (text.isBlank() || thinking) return
            if (pin.isBlank()) { signIn = true; return }
            input = ""
            val selectedAttachment = attachmentName
            messages.add(ChatMessage(true, text, selectedAttachment))
            KimPrefs.appendMessage(context, activeUser, conversationId, true, text)
            historyRefresh++
            thinking = true
            executor.execute {
                val raw = runCatching { KimClient(baseUrl, pin, activeUser).chat(text, conversationId, "Android phone for $activeUser; mobile apps, notifications, media, volume, flashlight, microphone, and phone status are available", attachmentName, attachmentText) }
                    .getOrElse { "500: ${it.message ?: "Kim is unavailable"}" }
                val body = raw.substringAfter(": ", raw)
                val reply = runCatching {
                    val json = JSONObject(body)
                    json.optString("message").ifBlank { json.optString("error").ifBlank { body } }
                }.getOrDefault(body)
                val musicJob = Regex("music-[a-f0-9]{8}").find(reply)?.value
                runOnUiThread { messages.add(ChatMessage(false, reply, musicJobId = musicJob)); KimPrefs.appendMessage(context, activeUser, conversationId, false, reply); historyRefresh++; attachmentName = null; attachmentText = null; thinking = false }
                if (musicJob != null) watchMusic(musicJob, context, pin, activeUser, messages, conversationId) { historyRefresh++ }
            }
        }

        LaunchedEffect(messages.size) { if (messages.isNotEmpty()) listState.animateScrollToItem(messages.lastIndex) }

        ModalNavigationDrawer(
            drawerState = drawer,
            drawerContent = {
                ModalDrawerSheet(drawerContainerColor = KimPanel) {
                    Text("Kim", fontSize = 25.sp, fontWeight = FontWeight.Bold, color = Color.White, modifier = Modifier.padding(24.dp))
                    NavigationDrawerItem(label = { Text("New chat") }, selected = false, onClick = { conversationId = KimPrefs.newChatId(context, activeUser); input = ""; scope.launch { drawer.close() } }, modifier = Modifier.padding(horizontal = 12.dp))
                    chatHistory.take(8).forEach { chat ->
                        NavigationDrawerItem(label = { Text(chat.title.ifBlank { "New chat" }, maxLines = 1) }, selected = chat.id == conversationId, onClick = { conversationId = chat.id; KimPrefs.setCurrentChat(context, activeUser, chat.id); scope.launch { drawer.close() } }, modifier = Modifier.padding(horizontal = 12.dp))
                    }
                    NavigationDrawerItem(label = { Text("Approvals") }, selected = false, onClick = { actionMenu = true; scope.launch { drawer.close() } }, modifier = Modifier.padding(horizontal = 12.dp))
                    Spacer(Modifier.weight(1f))
                    NavigationDrawerItem(label = { Text("Settings") }, selected = false, icon = { Icon(Icons.Default.Settings, null) }, onClick = { signIn = true; scope.launch { drawer.close() } }, modifier = Modifier.padding(12.dp))
                }
            }
        ) {
            Scaffold(
                containerColor = KimBackground,
                topBar = {
                    TopAppBar(
                        title = { Text("Kim", fontWeight = FontWeight.SemiBold) },
                        navigationIcon = { IconButton(onClick = { scope.launch { drawer.open() } }) { Icon(Icons.Default.Menu, "Menu") } },
                        actions = {
                            Box {
                                Button(onClick = { profileMenu = true }, colors = ButtonDefaults.buttonColors(containerColor = Color.Transparent), contentPadding = PaddingValues(horizontal = 10.dp)) { Text(activeUser, color = KimMuted) }
                                DropdownMenu(expanded = profileMenu, onDismissRequest = { profileMenu = false }) {
                                    KimPrefs.users.forEach { user -> DropdownMenuItem(text = { Text("Using Kim as $user") }, onClick = { activeUser = user; conversationId = KimPrefs.currentChatId(context, user); pin = KimPrefs.pin(context, user); signIn = pin.isBlank(); KimPrefs.setActiveUser(context, user); profileMenu = false }) }
                                }
                            }
                            IconButton(onClick = { actionMenu = true }) { Icon(Icons.Default.MoreVert, "More") }
                        }
                    )
                }
            ) { padding ->
                Column(Modifier.fillMaxSize().padding(padding).navigationBarsPadding()) {
                    LazyColumn(state = listState, modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 18.dp), contentPadding = PaddingValues(vertical = 24.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
                        if (messages.isEmpty()) item { Welcome() }
                        items(messages) { message -> MessageBubble(message) { downloadMusic(context, pin, activeUser, it) } }
                        if (thinking) item { MessageBubble(ChatMessage(false, "Kim is thinking…")) }
                    }
                    if (attachmentName != null) Text("Attached: $attachmentName", color = KimMuted, modifier = Modifier.padding(horizontal = 22.dp, vertical = 4.dp))
                    Composer(input, { input = it }, { send() }, {
                        if (voiceActive) { KimVoiceService.stop(context); voiceActive = false }
                        else if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) context.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 5)
                        else { context.startForegroundService(Intent(context, KimVoiceService::class.java)); voiceActive = true }
                    }, { actionMenu = true }, voiceActive)
                }
            }
        }

        if (actionMenu) {
            DropdownMenu(expanded = true, onDismissRequest = { actionMenu = false }) {
                DropdownMenuItem(text = { Text("Prepare my day") }, onClick = { input = "Prepare my day"; actionMenu = false })
                DropdownMenuItem(text = { Text("Show my calendar") }, onClick = { input = "Show my calendar"; actionMenu = false })
                DropdownMenuItem(text = { Text("Review requests") }, onClick = { input = "Review my requests"; actionMenu = false })
                DropdownMenuItem(text = { Text("Attach a file") }, onClick = { filePicker.launch(arrayOf("*/*")); actionMenu = false })
                DropdownMenuItem(text = { Text("Connect PropAI") }, onClick = {
                    actionMenu = false
                    executor.execute {
                        val raw = runCatching { KimClient(baseUrl, pin, activeUser).propaiStart() }.getOrElse { "500: ${it.message ?: "connection failed"}" }
                        val url = runCatching { JSONObject(raw.substringAfter(": ", raw)).optString("auth_url") }.getOrNull().orEmpty()
                        runOnUiThread {
                            if (url.isNotBlank()) context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                            else messages.add(ChatMessage(false, "PropAI connection could not start: ${raw.substringAfter(": ")}"))
                        }
                    }
                })
                DropdownMenuItem(text = { Text("Disconnect PropAI") }, onClick = {
                    actionMenu = false
                    executor.execute {
                        val raw = runCatching { KimClient(baseUrl, pin, activeUser).propaiDisconnect() }.getOrElse { "500: ${it.message ?: "connection failed"}" }
                        runOnUiThread { messages.add(ChatMessage(false, if (raw.startsWith("200:")) "PropAI MCP disconnected." else "PropAI disconnect failed: ${raw.substringAfter(": ")}")) }
                    }
                })
                DropdownMenuItem(text = { Text("Connect to desktop") }, onClick = {
                    actionMenu = false
                    executor.execute {
                        val result = runCatching { KimClient(baseUrl, pin, activeUser).connectDesktop() }
                            .getOrElse { "500: ${it.message ?: "connection failed"}" }
                        runOnUiThread { messages.add(ChatMessage(false, if (result.startsWith("200:")) { val body = result.substringAfter(": "); val connected = runCatching { JSONObject(body).optBoolean("connected", false) }.getOrElse { body.contains("\"connected\": true") }; if (connected) "Desktop connection is ready. Kim can use the connected desktop when you ask." else "Desktop relay is offline. Start Kim on the laptop first." } else "Desktop connection failed: ${result.substringAfter(": ")}")) }
                    }
                    context.startForegroundService(Intent(context, KimForegroundService::class.java))
                })
                DropdownMenuItem(text = { Text("Phone permissions") }, onClick = { actionMenu = false; context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) })
                DropdownMenuItem(text = { Text("Settings / connect phone") }, onClick = { signIn = true; actionMenu = false })
            }
        }
        if (signIn) {
            AlertDialog(onDismissRequest = { signIn = false }, title = { Text("Connect $activeUser to Kim") }, text = { OutlinedTextField(value = pin, onValueChange = { pin = it }, label = { Text("Private workspace key") }, visualTransformation = PasswordVisualTransformation(), singleLine = true) }, confirmButton = { Button(onClick = { connect(activeUser, pin); signIn = false }) { Text("Connect") } }, dismissButton = { Button(onClick = { signIn = false }) { Text("Later") } })
        }
    }

    private fun connect(user: String, value: String) {
        val clean = value.trim()
        if (clean.isBlank()) return
        executor.execute {
            val result = runCatching { KimClient(baseUrl, clean, user).getStatus() }
            if (result.isSuccess && result.getOrThrow().startsWith("200:")) { KimPrefs.savePin(this, user, clean); KimPrefs.setActiveUser(this, user) }
        }
    }

    private fun watchMusic(jobId: String, context: android.content.Context, token: String, user: String, messages: MutableList<ChatMessage>, conversationId: String, onUpdated: () -> Unit) {
        executor.execute {
            repeat(60) {
                Thread.sleep(3000)
                val raw = runCatching { KimClient(baseUrl, token, user).musicStatus(jobId) }.getOrNull() ?: return@repeat
                val body = raw.substringAfter(": ", raw)
                val status = runCatching { JSONObject(body).optJSONObject("job")?.optString("status") ?: "" }.getOrDefault("")
                if (status == "completed" || status == "failed") {
                    val text = if (status == "completed") "Your music is ready. Tap Download generated music below." else "Music generation failed: ${runCatching { JSONObject(body).optJSONObject("job")?.optString("error") }.getOrNull() ?: "unknown error"}"
                    runOnUiThread { messages.add(ChatMessage(false, text, musicJobId = if (status == "completed") jobId else null)); KimPrefs.appendMessage(context, user, conversationId, false, text); onUpdated() }
                    return@execute
                }
            }
        }
    }

    private fun downloadMusic(context: android.content.Context, token: String, user: String, jobId: String) {
        val request = DownloadManager.Request(Uri.parse("$baseUrl/v1/music/$jobId/download"))
            .setTitle("Kim music")
            .setDescription("Downloading generated music")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalFilesDir(context, android.os.Environment.DIRECTORY_MUSIC, "$jobId.mp3")
            .addRequestHeader("X-Kim-Pin", token)
            .addRequestHeader("X-Kim-User", user)
        context.getSystemService(DownloadManager::class.java).enqueue(request)
    }
}

@Composable
private fun Welcome() {
    Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth().padding(top = 100.dp)) {
        Box(Modifier.size(76.dp).background(Color(0xFFB9C0CA), RoundedCornerShape(24.dp)), contentAlignment = Alignment.Center) { Box(Modifier.size(44.dp).background(Color(0xFF111318), RoundedCornerShape(15.dp))) }
        Spacer(Modifier.height(24.dp))
        Text("How can I help you?", fontSize = 30.sp, fontWeight = FontWeight.SemiBold, color = Color.White)
        Spacer(Modifier.height(8.dp))
        Text("Ask Kim anything", color = KimMuted, fontSize = 16.sp)
    }
}

@Composable
private fun MessageBubble(message: ChatMessage, onDownload: (String) -> Unit = {}) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (message.user) Arrangement.End else Arrangement.Start) {
        Column(horizontalAlignment = if (message.user) Alignment.End else Alignment.Start) {
            Text(message.text, color = if (message.user) Color(0xFF17191D) else Color(0xFFE5E7EB), fontSize = 16.sp, modifier = Modifier.background(if (message.user) Color(0xFFE7E9ED) else KimPanel, RoundedCornerShape(20.dp)).padding(horizontal = 16.dp, vertical = 12.dp))
            if (message.attachmentName != null) Text("Attached: ${message.attachmentName}", color = KimMuted, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 12.dp, vertical = 3.dp))
            if (message.musicJobId != null) Button(onClick = { onDownload(message.musicJobId) }, modifier = Modifier.padding(top = 6.dp)) { Text("Download generated music") }
        }
    }
}

@Composable
private fun Composer(value: String, onValue: (String) -> Unit, onSend: () -> Unit, onMic: () -> Unit, onMenu: () -> Unit, voiceActive: Boolean = false) {
    Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
        Box {
            IconButton(onClick = onMenu, modifier = Modifier.size(48.dp)) { Icon(Icons.Default.Add, "Actions", tint = KimMuted) }
        }
        Spacer(Modifier.width(6.dp))
        OutlinedTextField(value = value, onValueChange = onValue, modifier = Modifier.weight(1f), placeholder = { Text("Message Kim…", color = KimMuted) }, minLines = 1, maxLines = 6, shape = RoundedCornerShape(24.dp), colors = OutlinedTextFieldDefaults.colors(unfocusedContainerColor = KimPanel, focusedContainerColor = KimPanel, unfocusedBorderColor = KimBorder, focusedBorderColor = Color(0xFF8B93A3), unfocusedTextColor = Color.White, focusedTextColor = Color.White))
        IconButton(onClick = onMic, modifier = Modifier.size(48.dp)) { Icon(if (voiceActive) Icons.Default.Stop else Icons.Default.Mic, if (voiceActive) "Stop Kim" else "Voice", tint = if (voiceActive) Color(0xFFFF7777) else KimMuted) }
        IconButton(onClick = onSend, modifier = Modifier.size(48.dp)) { Icon(Icons.AutoMirrored.Filled.Send, "Send", tint = Color.White) }
    }
}

@Composable
private fun KimTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = darkColorScheme(background = KimBackground, surface = KimPanel, primary = Color(0xFFE7E9ED), onSurface = Color.White), content = content)
}
