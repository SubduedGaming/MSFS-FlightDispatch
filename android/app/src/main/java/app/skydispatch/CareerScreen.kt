package app.skydispatch

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/** First-run setup: the same choices as the old desktop wizard, sent to `POST /api/v1/career`. */
@Composable
fun CareerScreen(c: Connection, onForget: () -> Unit, onCreated: () -> Unit) {
    val api = remember(c) { Api(c.baseUrl, c.token) }
    var options by remember { mutableStateOf<CareerOptions?>(null) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var attempt by remember { mutableIntStateOf(0) }
    LaunchedEffect(c, attempt) {
        loadError = null
        try { options = api.careerOptions() } catch (e: ApiException) { loadError = e.message }
    }
    val o = options
    when {
        o != null -> CareerFormUi(api, o, onCreated)
        loadError != null -> Centered {
            Text(loadError!!)
            Button(onClick = { attempt++ }) { Text("Try again") }
            OutlinedButton(onClick = onForget) { Text("Forget this PC") }
        }
        else -> Centered { CircularProgressIndicator() }
    }
}

@Composable
private fun CareerFormUi(api: Api, o: CareerOptions, onCreated: () -> Unit) {
    val d = o.defaults
    var name by remember { mutableStateOf(d.name) }
    var home by remember { mutableStateOf(d.home) }
    var callsign by remember { mutableStateOf(d.callsign) }
    var aircraft by remember { mutableStateOf(d.aircraft.takeIf { id -> o.starters.any { it.id == id && it.installed } }
        ?: o.starters.firstOrNull { it.installed }?.id ?: "") }
    var experience by remember { mutableStateOf(d.experience) }
    var difficulty by remember { mutableStateOf(d.difficulty) }
    var currency by remember { mutableStateOf(d.currency.ifBlank { o.currencies.firstOrNull() ?: "" }) }
    var balance by remember { mutableStateOf(if (d.balance > 0) d.balance.toLong().toString() else "") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(Modifier.verticalScroll(rememberScrollState()).statusBarsPadding().imePadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Start your career", style = MaterialTheme.typography.headlineSmall)
        OutlinedTextField(name, { name = it.take(40) }, label = { Text("Pilot name") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(home, { home = it.uppercase().filter(Char::isLetterOrDigit).take(4) },
            label = { Text("Home airport (ICAO, e.g. EGLL)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(callsign, { callsign = it.uppercase().filter(Char::isLetterOrDigit).take(8) },
            label = { Text("Callsign") }, singleLine = true, modifier = Modifier.fillMaxWidth())

        Choices("Starter aircraft", o.starters.map { Triple(it.id, if (it.installed) it.name else "${it.name} (not installed)", it.installed) },
            aircraft) { aircraft = it }
        Choices("Flying experience", o.experience.map { Triple(it.id, it.label, true) }, experience) { experience = it }
        Choices("Difficulty", o.difficulty.map { Triple(it, it.replaceFirstChar(Char::uppercase), true) }, difficulty) { difficulty = it }
        if (o.currencies.isNotEmpty()) Choices("Currency", o.currencies.map { Triple(it, it, true) }, currency) { currency = it }
        OutlinedTextField(balance, { balance = it.filter { ch -> ch.isDigit() || ch == '.' } },
            label = { Text("Starting balance (optional)") }, singleLine = true, modifier = Modifier.fillMaxWidth())

        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(enabled = !busy, modifier = Modifier.fillMaxWidth(), onClick = {
            val form = CareerForm(name, home, callsign, aircraft, experience, difficulty, currency, balance)
            val problem = form.problem()
            if (problem != null) { error = problem; return@Button }
            busy = true
            error = null
            scope.launch {
                try { api.createCareer(form.toJson()); onCreated() }
                catch (e: ApiException) { error = e.message }
                finally { busy = false }
            }
        }) { Text(if (busy) "Starting..." else "Start career") }
    }
}

@Composable
private fun Choices(title: String, options: List<Triple<String, String, Boolean>>, selected: String, onSelect: (String) -> Unit) {
    if (options.isEmpty()) return
    Spacer(Modifier.height(8.dp))
    Text(title, style = MaterialTheme.typography.titleSmall)
    options.forEach { (id, label, enabled) ->
        Row(Modifier.fillMaxWidth().selectable(selected = id == selected, enabled = enabled, role = Role.RadioButton) { onSelect(id) },
            verticalAlignment = Alignment.CenterVertically) {
            RadioButton(selected = id == selected, onClick = null, enabled = enabled)
            Text(label, Modifier.padding(start = 12.dp, top = 8.dp, bottom = 8.dp))
        }
    }
}
