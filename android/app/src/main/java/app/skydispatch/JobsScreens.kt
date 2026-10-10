package app.skydispatch

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp

@Composable
fun JobsScreen(go: (Dest) -> Unit) {
    var tab by remember { mutableIntStateOf(0) }
    Column(Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = tab) {
            Tab(tab == 0, { tab = 0 }, text = { Text("Job market") })
            Tab(tab == 1, { tab = 1 }, text = { Text("Companies") })
        }
        if (tab == 0) Market(go) else Companies(go)
    }
}

@Composable
private fun Market(go: (Dest) -> Unit) {
    val act = rememberAct()
    Loader("market", load = { it.get("market") }) { m ->
        ScreenColumn {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Title("Freelance jobs")
                OutlinedButton(enabled = !m.bool("busy"), onClick = { act { it.post("market/refresh") } }) { Text(if (m.bool("busy")) "Looking..." else "Refresh") }
            }
            val jobs = m.list("jobs")
            if (jobs.isEmpty()) Text("No jobs right now. Try Refresh.")
            jobs.forEach { j ->
                InfoCard(onClick = { go(Dest.Market(j.int("id"))) }) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Pill(j.str("kind_label"))
                        Text(j.str("payout"), style = MaterialTheme.typography.titleMedium)
                    }
                    Text("${j.str("origin")} to ${j.str("dest")}", style = MaterialTheme.typography.titleSmall)
                    Muted("${j.str("origin_name")} to ${j.str("dest_name")}")
                    Muted("${j.str("distance")}  |  ${j.str("load")}  |  due in ${j.str("deadline")}")
                }
            }
        }
    }
}

@Composable
fun MarketDetail(id: Int, back: () -> Unit) {
    val act = rememberAct()
    var plane by remember { mutableStateOf<Int?>(null) }
    var asked by remember { mutableStateOf(false) }
    Loader("market/$id", load = { it.get("market/$id") }) { j ->
        val planes = j.list("planes")
        val eligible = planes.filter { it.bool("ok") }
        val chosen = plane ?: eligible.firstOrNull()?.int("id")
        ScreenColumn {
            Title(j.str("title"))
            InfoCard {
                Row2("Pays", j.str("payout")); Row2("Distance", j.str("distance")); Row2("Load", j.str("load"))
                Row2("Deadline", j.str("deadline")); Row2("Client", j.str("client"))
                Muted("${j.str("origin_name")} (${j.str("origin")}) to ${j.str("dest_name")} (${j.str("dest")})")
                if (j.str("dest_info").isNotEmpty()) Muted("Destination: " + j.str("dest_info"))
            }
            Text(j.str("briefing"))
            if (planes.isNotEmpty()) {
                Heading("Aircraft")
                planes.forEach { p ->
                    Row(Modifier.fillMaxWidth().selectable(chosen == p.int("id"), p.bool("ok"), Role.RadioButton) { plane = p.int("id") }, verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(chosen == p.int("id"), onClick = null, enabled = p.bool("ok"))
                        Column(Modifier.padding(start = 12.dp, top = 6.dp, bottom = 6.dp)) {
                            Text(p.str("label"))
                            if (!p.bool("ok")) Muted(p.str("why"))
                        }
                    }
                }
            } else if (j.str("provided_type").isNotEmpty()) Muted("The company provides the aircraft.")
            val busy = j.bool("active")
            if (busy) Text("Finish your current job first.", color = toneColor("warn"))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(enabled = !busy && (chosen != null || j.str("provided_type").isNotEmpty()), onClick = {
                    act { it.post("market/$id/accept", jsonOf("aircraft_id" to chosen)); back() }
                }) { Text("Accept") }
                OutlinedButton(onClick = { act { it.post("market/$id/decline"); back() } }) { Text("Decline") }
            }
            OutlinedButton(enabled = !asked, onClick = { asked = true; act { it.post("market/$id/ask") } }) {
                Text(if (asked) "Asked. See Messages" else "Ask dispatch if it is worth it")
            }
        }
    }
}

@Composable
private fun Companies(go: (Dest) -> Unit) {
    Loader("jobboard", load = { it.get("jobboard") }) { b ->
        ScreenColumn {
            Title("Companies")
            Tiles(b.list("tiles"))
            Muted(b.str("classes"))
            b.list("employers").forEach { e ->
                InfoCard(onClick = { go(Dest.Employer(e.str("id"))) }) {
                    Text(e.str("name"), style = MaterialTheme.typography.titleSmall)
                    Muted(e.str("tagline"))
                    Pill(e.str("state"), e.str("tone").ifEmpty { null })
                }
            }
            val apps = b.list("applications")
            if (apps.isNotEmpty()) {
                Heading("Your applications")
                apps.forEach { a -> InfoCard { Row2(a.str("company"), a.str("result")); Muted(a.str("date")); if (a.str("details").isNotEmpty()) Muted(a.str("details")) } }
            }
        }
    }
}

@Composable
fun EmployerDetail(id: String, go: (Dest) -> Unit) {
    val act = rememberAct()
    var resign by remember { mutableStateOf(false) }
    Loader("employer/$id", load = { it.get("employer/$id") }) { e ->
        ScreenColumn {
            Title(e.str("name"))
            Muted(e.str("tagline"))
            Pill(e.str("state"), e.str("tone").ifEmpty { null })
            Text(e.str("blurb"))
            InfoCard {
                Row2("Base", e.str("base")); Row2("Work", e.str("work")); Row2("Aircraft", e.str("fleet"))
                Muted(e.str("pay"))
                Muted(e.str("hiring"))
                if (e.str("record").isNotEmpty()) Muted(e.str("record"))
                if (e.str("note").isNotEmpty()) Text(e.str("note"), color = toneColor("warn"))
            }
            val checks = e.list("checks")
            if (checks.isNotEmpty()) {
                Heading("Requirements")
                checks.forEach { c -> Row2("${c.str("label")} (needs ${c.str("required")})", c.str("actual"), if (c.bool("met")) "good" else "bad") }
            }
            if (e.bool("can_apply")) Button(onClick = {
                act { api ->
                    val r = api.post("employer/$id/apply")
                    Hub.toast(if (r.bool("accepted")) "good" else "warn", r.str("message"))
                    if (r.bool("accepted")) go(Dest.Chat(r.str("thread")))
                }
            }) { Text("Apply") }
            if (e.bool("employed")) {
                Button(onClick = { go(Dest.Chat(e.str("thread"))) }) { Text("Open conversation") }
                OutlinedButton(onClick = { resign = true }) { Text("Resign") }
            }
        }
    }
    if (resign) Confirm("Resign?", "You will leave this company.", "Resign", { resign = false }) { act { it.post("employer/$id/resign") } }
}
