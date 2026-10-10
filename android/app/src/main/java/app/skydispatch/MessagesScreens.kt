package app.skydispatch

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun MessagesScreen(go: (Dest) -> Unit) {
    Loader("threads", load = { it.get("threads") }) { t ->
        ScreenColumn {
            Title("Messages")
            t.list("threads").forEach { th ->
                InfoCard(onClick = { go(Dest.Chat(th.str("id"))) }) {
                    Text(th.str("name"), style = MaterialTheme.typography.titleSmall)
                    Muted(if (th.bool("busy")) "Typing..." else th.str("preview"))
                }
            }
        }
    }
}

@Composable
fun ChatScreen(id: String) {
    val act = rememberAct()
    var clear by remember { mutableStateOf(false) }
    // Tell the PC this conversation is on screen so it speaks here, and stop when we leave.
    DisposableEffect(id) { Hub.view(id); onDispose { Hub.view(null) } }
    Loader("thread/$id", load = { it.get("thread/$id?open=1") }) { t ->
        val scroll = rememberScrollState()
        val messages = t.list("messages")
        LaunchedEffect(messages.size, t.bool("busy")) { scroll.animateScrollTo(scroll.maxValue) }
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                Column(Modifier.weight(1f)) {
                    Text(t.str("name"), style = MaterialTheme.typography.titleMedium)
                    Muted(t.str("sub"))
                }
                TextButton(onClick = { clear = true }) { Text("Clear") }
            }
            Column(Modifier.weight(1f).verticalScroll(scroll).padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                MessageBubbles(messages, id, t.bool("can_act"))
                t.list("chips").let { chips ->
                    if (chips.isNotEmpty()) {
                        Muted("How long do you have?")
                        QuickButtons(chips.map { it.str("label") }) { i -> act { it.post("thread/$id/availability", jsonOf("minutes" to chips[i].int("minutes"))) } }
                    }
                }
                if (t.bool("can_ask_time")) OutlinedButton(onClick = { act { it.post("thread/$id/ask_time") } }) { Text("Ask about work") }
                if (t.bool("busy")) Muted("Typing...")
            }
            ChatInput(id, t.bool("busy")) { text -> act { it.post("thread/$id/send", jsonOf("text" to text)) } }
        }
    }
    if (clear) Confirm("Clear this conversation?", "The messages are deleted.", "Clear", { clear = false }) { act { it.post("thread/$id/clear") } }
}
