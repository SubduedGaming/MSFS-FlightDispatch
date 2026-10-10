package app.skydispatch

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject

/** The server marks values good / warn / bad / accent / muted; these are the colours the phone shows for them. */
@Composable
fun toneColor(tone: String?): Color = when (tone) {
    "good" -> Color(0xFF2E7D32)
    "warn" -> Color(0xFFB26A00)
    "bad" -> MaterialTheme.colorScheme.error
    "accent" -> MaterialTheme.colorScheme.primary
    "muted" -> MaterialTheme.colorScheme.onSurfaceVariant
    else -> MaterialTheme.colorScheme.onSurface
}

/**
 * Fetch something from the PC and show it. It reads again when the PC says something changed (unless [live] is false)
 * and keeps the old content on screen while it does, so screens do not flicker.
 */
@Composable
fun <T> Loader(vararg keys: Any?, live: Boolean = true, load: suspend (Api) -> T, content: @Composable (T) -> Unit) {
    var data by remember { mutableStateOf<T?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var attempt by remember { mutableIntStateOf(0) }
    val version by Hub.version.collectAsState()
    LaunchedEffect(attempt, if (live) version else 0, keys.toList()) {
        val api = Hub.api ?: return@LaunchedEffect
        try { data = load(api); error = null } catch (e: ApiException) { error = e.message }
    }
    LaunchedEffect(error, data == null) {                 // a screen that could not load tries again by itself
        if (error != null && data == null) { delay(3000); attempt++ }
    }
    val d = data
    when {
        d != null -> content(d)
        error != null -> Centered {
            Text(error ?: "", color = MaterialTheme.colorScheme.error)
            Button(onClick = { attempt++ }) { Text("Try again") }
        }
        else -> Centered { CircularProgressIndicator() }
    }
}

/** Run something against the PC from a button. Problems come back as a message the player can read. */
@Composable
fun rememberAct(): (suspend (Api) -> Unit) -> Unit {
    val scope = rememberCoroutineScope()
    return remember {
        { block ->
            scope.launch {
                try { Hub.api?.let { block(it) } } catch (e: ApiException) { Hub.toast("bad", e.message ?: "Something went wrong.") }
            }
        }
    }
}

@Composable
fun ScreenColumn(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
}

@Composable
fun Title(text: String) = Text(text, style = MaterialTheme.typography.headlineSmall)

@Composable
fun Heading(text: String) = Text(text, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 8.dp))

@Composable
fun Muted(text: String) = Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

@Composable
fun InfoCard(modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val inner: @Composable ColumnScope.() -> Unit = { Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp), content = content) }
    if (onClick != null) Card(onClick = onClick, modifier = modifier.fillMaxWidth(), content = inner)
    else Card(modifier = modifier.fillMaxWidth(), content = inner)
}

/** Label/value tiles, two to a row. */
@Composable
fun Tiles(tiles: List<JsonObject>) {
    tiles.chunked(2).forEach { row ->
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            row.forEach { t ->
                Card(Modifier.weight(1f)) {
                    Column(Modifier.padding(12.dp)) {
                        Muted(t.str("label"))
                        Text(t.str("value"), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, color = toneColor(t.str("tone").ifEmpty { null }))
                        if (t.str("hint").isNotEmpty()) Muted(t.str("hint"))
                    }
                }
            }
            if (row.size == 1) Spacer(Modifier.weight(1f))
        }
    }
}

@Composable
fun Pill(text: String, tone: String? = null) {
    val c = toneColor(tone)
    Text(text, color = c, style = MaterialTheme.typography.labelMedium,
        modifier = Modifier.background(c.copy(alpha = 0.12f), RoundedCornerShape(50)).padding(horizontal = 10.dp, vertical = 3.dp))
}

@Composable
fun Row2(label: String, value: String, tone: String? = null) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, color = toneColor(tone), fontWeight = FontWeight.Medium)
    }
}

@Composable
fun BarMeter(fraction: Float, tone: String? = null) {
    LinearProgressIndicator(progress = { fraction.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth(), color = toneColor(tone ?: "accent"))
}

@Composable
fun Confirm(title: String, text: String, ok: String, onDismiss: () -> Unit, onOk: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, title = { Text(title) }, text = { Text(text) },
        confirmButton = { TextButton(onClick = { onDismiss(); onOk() }) { Text(ok) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}

/** A scrolling row of small buttons; [onClick] gets the index of the one pressed. */
@Composable
fun QuickButtons(labels: List<String>, onClick: (Int) -> Unit) {
    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        labels.forEachIndexed { i, l -> AssistChip(onClick = { onClick(i) }, label = { Text(l) }) }
    }
}

@Composable
fun Centered(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally, content = content)
}
