package app.skydispatch

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun HomeScreen(go: (Dest) -> Unit, openFlight: () -> Unit) {
    Loader("dashboard", load = { it.get("dashboard") }) { d ->
        ScreenColumn {
            Title(d.str("hello"))
            Muted(d.str("sub"))
            d.list("alerts").forEach { a ->
                InfoCard { Text(a.str("text"), color = toneColor(a.str("level"))) }
            }
            Tiles(d.list("tiles"))
            d.obj("job")?.let { j ->
                Heading("Current job")
                InfoCard(onClick = openFlight) {
                    Text(j.str("title"), style = MaterialTheme.typography.titleSmall)
                    Muted(j.str("info"))
                    TextButton(onClick = openFlight) { Text("Open flight") }
                }
            }
            d.obj("systems")?.let { s ->
                Heading("Systems")
                InfoCard {
                    Row2("Simulator", s.str("sim"))
                    Row2("AI dispatcher", s.str("ai"))
                    Row2("Voice", s.str("voice"))
                }
            }
            val flights = d.list("flights")
            if (flights.isNotEmpty()) {
                Heading("Recent flights")
                flights.forEach { f ->
                    InfoCard(onClick = { go(Dest.Logbook) }) {
                        Row2(f.str("route"), f.str("net"), f.str("net_tone"))
                        Muted("${f.str("date")}  |  ${f.str("aircraft")}  |  landing ${f.str("landing")}  |  score ${f.str("score")}")
                    }
                }
            }
        }
    }
}

@Composable
fun FlightScreen(openJobs: () -> Unit) {
    DisposableEffect(Unit) { Hub.view("copilot"); onDispose { Hub.view(null) } }
    val live by Hub.live.collectAsState()
    val act = rememberAct()
    var confirm by remember { mutableStateOf<String?>(null) }

    Loader("flight", load = { it.get("flight") }) { f ->
        val cells = live?.obj("cells") ?: f.obj("cells")
        val phase = live?.str("phase")?.ifEmpty { null } ?: f.str("phase")
        val progress = (live?.dbl("progress", f.dbl("progress")) ?: f.dbl("progress")).toFloat()
        val eta = live?.str("eta")?.ifEmpty { null } ?: f.str("eta")
        ScreenColumn {
            Title("Flight")
            if (!f.bool("has_job")) {
                Text("No flight in progress. Accept a job to get started.")
                Button(onClick = openJobs) { Text("Find a job") }
            } else {
                f.obj("job")?.let { j ->
                    InfoCard { Text(j.str("title"), style = MaterialTheme.typography.titleSmall); Muted(j.str("info")) }
                }
                InfoCard {
                    Row2("Phase", phase)
                    BarMeter(progress)
                    Muted(eta)
                }
                cells?.let { c ->
                    listOf("alt" to "Altitude", "ias" to "Airspeed", "gs" to "Ground speed", "vs" to "Vertical speed",
                        "hdg" to "Heading", "fuel" to "Fuel", "g" to "G", "dist" to "Flown").chunked(2).forEach { row ->
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            row.forEach { (k, label) ->
                                Card(Modifier.weight(1f)) { Column(Modifier.padding(10.dp)) { Muted(label); Text(c.str(k, "-"), style = MaterialTheme.typography.titleMedium) } }
                            }
                        }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (f.bool("can_end")) Button(onClick = { confirm = "end" }) { Text("End flight") }
                    OutlinedButton(onClick = { confirm = "abandon" }) { Text("Abandon job") }
                }
                if (f.bool("can_demo")) OutlinedButton(onClick = { act { it.post("flight/demo") } }) { Text("Fly it for me (demo)") }
                val events = f.list("events")
                if (events.isNotEmpty()) {
                    Heading("Events")
                    events.takeLast(12).reversed().forEach { e -> Muted("${e.str("kind").replace('_', ' ')}: ${e.str("detail")}") }
                }
                PlanSection()
            }
            f.obj("copilot")?.let { CopilotSection(it) }
        }
    }
    when (confirm) {
        "end" -> Confirm("End the flight?", "The flight is scored as it stands.", "End flight", { confirm = null }) { act { it.post("flight/end") } }
        "abandon" -> Confirm("Abandon the job?", "You will lose the job and may lose reputation.", "Abandon", { confirm = null }) { act { it.post("job/abandon") } }
    }
}

@Composable
private fun PlanSection() {
    val act = rememberAct()
    Loader("plan", load = { it.get("plan") }) { p ->
        if (!p.bool("has_job")) return@Loader
        Heading("Flight plan and loadout")
        p.obj("ofp")?.let { o ->
            InfoCard {
                Text(o.str("route"), style = MaterialTheme.typography.bodyMedium)
                Row2("Cruise", o.str("altitude")); Row2("Distance", o.str("distance")); Row2("Time en route", o.str("ete"))
                Row2("Block fuel", o.str("block_fuel")); Row2("Reserve", o.str("reserve_fuel")); Row2("Alternate", o.str("alternate"))
                if (o.bool("stale")) Text("This plan is old. Import a fresh one.", color = toneColor("warn"))
            }
        }
        p.obj("loadout")?.let { l ->
            InfoCard {
                Row2("Fuel", l.str("fuel")); Muted("From ${l.str("fuel_source")}")
                Row2("Payload", l.str("payload"))
                l.strings("notes").forEach { Muted(it) }
            }
        }
        p.obj("sim")?.let { s ->
            InfoCard {
                Row2("In the simulator", s.str("fuel") + "  |  " + s.str("payload"))
                Text(if (s.bool("matches")) "Loadout matches the plan." else "Loadout differs from the plan.", color = toneColor(if (s.bool("matches")) "good" else "warn"))
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (p.bool("user_set")) OutlinedButton(onClick = { act { it.post("plan/import") } }) { Text("Import SimBrief") }
            OutlinedButton(onClick = { act { it.post("plan/sync") } }) { Text("Load into sim") }
            if (p.bool("can_refuel")) OutlinedButton(onClick = { act { it.post("plan/refuel") } }) { Text("Refuel") }
        }
        if (!p.bool("user_set")) Muted("Add your SimBrief username in Settings to import plans.")
    }
}

@Composable
private fun CopilotSection(c: kotlinx.serialization.json.JsonObject) {
    if (!c.bool("enabled")) return
    val act = rememberAct()
    Heading("Co-pilot: ${c.str("name")}")
    val quick = c.list("quick")
    QuickButtons(quick.map { it.str("label") }) { i -> act { it.post("copilot/ask", jsonOf("quick" to quick[i].str("key"))) } }
    MessageBubbles(c.list("messages").takeLast(8), "copilot", canAct = false)
    ChatInput("copilot", c.bool("busy")) { text -> act { it.post("copilot/ask", jsonOf("text" to text)) } }
}
