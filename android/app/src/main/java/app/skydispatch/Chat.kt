package app.skydispatch

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** The messages of one conversation, newest last. Offers show Accept / Decline while they can still be answered. */
@Composable
fun MessageBubbles(messages: List<JsonObject>, thread: String, canAct: Boolean) {
    val act = rememberAct()
    messages.forEach { m ->
        val mine = m.str("role") == "user"
        Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
            Column(
                Modifier.widthIn(max = 320.dp)
                    .background(if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(14.dp))
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                val content = m.str("content")
                if (content.isNotBlank()) Text(content)
                m.obj("offer")?.let { o ->
                    Text(o.str("title"), style = MaterialTheme.typography.titleSmall)
                    if (o.str("aircraft").isNotEmpty()) Muted(o.str("aircraft"))
                    if (o.str("deadline").isNotEmpty()) Muted("Deadline " + o.str("deadline"))
                    if (o.str("status") == "offered" && canAct) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { act { it.post("offer/${o.int("id")}/accept", jsonOf("thread" to thread)) } }) { Text("Accept") }
                            OutlinedButton(onClick = { act { it.post("offer/${o.int("id")}/decline", jsonOf("thread" to thread)) } }) { Text("Decline") }
                        }
                    } else Pill(o.str("status").replaceFirstChar(Char::uppercase), if (o.str("status") == "accepted") "good" else "muted")
                }
            }
        }
    }
}

/** A text box with Send and a hold-to-talk microphone. Speech is recognised on the PC and sent as [thread]. */
@Composable
fun ChatInput(thread: String, busy: Boolean, onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    var recording by remember { mutableStateOf(false) }
    var processing by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val recorder = remember { Recorder() }
    val askPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        if (!it) Hub.toast("warn", "Allow the microphone to talk to your dispatcher.")
    }

    Column(Modifier.fillMaxWidth().padding(8.dp)) {
        if (busy || processing || recording) {
            Muted(when { recording -> "Listening... let go to send"; processing -> "Working out what you said..."; else -> "Typing..." })
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(text, { text = it }, Modifier.weight(1f), placeholder = { Text("Message") }, maxLines = 3)
            Button(enabled = text.isNotBlank(), onClick = { onSend(text.trim()); text = "" }) { Text("Send") }
            Surface(
                shape = RoundedCornerShape(50), color = if (recording) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.secondaryContainer,
                modifier = Modifier.size(48.dp).pointerInput(thread) {
                    detectTapGestures(onPress = {
                        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                            askPermission.launch(Manifest.permission.RECORD_AUDIO)
                            return@detectTapGestures
                        }
                        Hub.silence()
                        if (!recorder.start()) { Hub.toast("warn", "Could not open the microphone."); return@detectTapGestures }
                        recording = true
                        tryAwaitRelease()
                        recording = false
                        val wav = recorder.stop()
                        if (wav == null) { Hub.toast("info", "Hold the button while you speak."); return@detectTapGestures }
                        processing = true
                        scope.launch {
                            try { Hub.api?.transcribe(wav, thread) } catch (e: ApiException) {
                                Hub.toast("warn", if (e.status == 503) "The PC cannot understand speech. Type your message instead." else e.message ?: "Could not send that.")
                            } finally { processing = false }
                        }
                    })
                },
            ) { Box(contentAlignment = Alignment.Center) { Text("Mic", style = MaterialTheme.typography.labelMedium) } }
        }
    }
}
