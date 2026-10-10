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
fun MoreScreen(go: (Dest) -> Unit, version: String) {
    ScreenColumn {
        Title("More")
        listOf("Hangar" to Dest.Hangar, "Aircraft dealer" to Dest.Dealer, "Logbook" to Dest.Logbook,
            "Finances" to Dest.Finance, "Training and licences" to Dest.Training, "Settings" to Dest.Settings).forEach { (label, d) ->
            InfoCard(onClick = { go(d) }) { Text(label, style = MaterialTheme.typography.titleSmall) }
        }
        Muted("SkyDispatch $version")
    }
}

// ------------------------------------------------------------------------------------------------ hangar
@Composable
fun HangarScreen() {
    val act = rememberAct()
    var sell by remember { mutableStateOf<Pair<Int, String>?>(null) }
    var rename by remember { mutableStateOf<Pair<Int, String>?>(null) }
    Loader("hangar", load = { it.get("hangar") }) { h ->
        ScreenColumn {
            Title("Hangar")
            val fleet = h.list("fleet")
            if (fleet.isEmpty()) Text("You do not own any aircraft yet. Visit the dealer.")
            fleet.forEach { a ->
                val id = a.int("id")
                InfoCard {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text("${a.str("registration")}  ${a.str("nickname")}".trim(), style = MaterialTheme.typography.titleSmall)
                        Pill(a.str("status"), if (a.bool("airworthy")) "good" else "bad")
                    }
                    Text(a.str("type")); Muted("At ${a.str("location")}  |  ${a.str("hours")} flown  |  worth ${a.str("value")}")
                    Muted(a.str("specs"))
                    Muted("Condition ${a.int("condition")}%"); BarMeter(a.int("condition") / 100f, if (a.int("condition") < 50) "bad" else "good")
                    Muted("Fuel ${a.str("fuel_label")}"); BarMeter(a.int("fuel_pct") / 100f)
                    Muted("Next inspection in ${a.str("inspection_label")}"); BarMeter(a.int("inspection_pct") / 100f, if (a.int("inspection_pct") < 15) "warn" else "good")
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        if (a.bool("can_refuel")) OutlinedButton(onClick = { act { it.post("hangar/$id/refuel") } }, contentPadding = PaddingValues(horizontal = 10.dp)) { Text("Refuel") }
                        OutlinedButton(onClick = { act { it.post("hangar/$id/inspect") } }, contentPadding = PaddingValues(horizontal = 10.dp)) { Text("Inspect ${a.str("inspect_cost")}") }
                        if (a.bool("can_repair")) OutlinedButton(onClick = { act { it.post("hangar/$id/repair") } }, contentPadding = PaddingValues(horizontal = 10.dp)) { Text("Repair ${a.str("repair_cost")}") }
                    }
                    Row {
                        TextButton(onClick = { rename = id to a.str("nickname") }) { Text("Rename") }
                        TextButton(onClick = { sell = id to a.str("registration") }) { Text("Sell") }
                    }
                }
            }
            val flown = h.list("flown")
            if (flown.isNotEmpty()) {
                Heading("Other aircraft you have flown")
                flown.forEach { f -> InfoCard { Text(f.str("title")); Muted("${f.str("match")}  |  ${f.int("flights")} flights, ${f.str("time")}  |  last ${f.str("last")}") } }
            }
        }
    }
    sell?.let { (id, reg) -> Confirm("Sell $reg?", "It is sold for its current value.", "Sell", { sell = null }) { act { it.post("hangar/$id/sell") } } }
    rename?.let { (id, nick) ->
        var text by remember(id) { mutableStateOf(nick) }
        AlertDialog(onDismissRequest = { rename = null }, title = { Text("Nickname") },
            text = { OutlinedTextField(text, { text = it.take(24) }, singleLine = true) },
            confirmButton = { TextButton(onClick = { rename = null; act { it.post("hangar/$id/rename", jsonOf("nickname" to text)) } }) { Text("Save") } },
            dismissButton = { TextButton(onClick = { rename = null }) { Text("Cancel") } })
    }
}

@Composable
fun DealerScreen() {
    val act = rememberAct()
    var buying by remember { mutableStateOf<kotlinx.serialization.json.JsonObject?>(null) }
    Loader("dealer", live = false, load = { it.get("dealer") }) { d ->
        ScreenColumn {
            Title("Aircraft dealer")
            Muted("Aircraft are delivered to ${d.str("home")}.")
            d.list("aircraft").forEach { a ->
                InfoCard(onClick = { buying = a }) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(a.str("name"), style = MaterialTheme.typography.titleSmall)
                        Text(a.str("price"))
                    }
                    Muted("${a.str("category")}  |  ${a.int("seats")} seats  |  ${a.str("cargo")}  |  ${a.str("range")}  |  ${a.str("cruise")}")
                    Muted("Used from ${a.str("used_price")}")
                }
            }
        }
        buying?.let { a ->
            AlertDialog(onDismissRequest = { buying = null }, title = { Text(a.str("name")) },
                text = { Text("New ${a.str("price")} or used ${a.str("used_price")}. Delivered to ${d.str("home")}.") },
                confirmButton = { TextButton(onClick = { buying = null; act { it.post("hangar/buy", jsonOf("type_id" to a.str("id"), "location" to d.str("home"), "used" to false)); Hub.toast("good", "Purchased") } }) { Text("Buy new") } },
                dismissButton = { Row { TextButton(onClick = { buying = null; act { it.post("hangar/buy", jsonOf("type_id" to a.str("id"), "location" to d.str("home"), "used" to true)) } }) { Text("Buy used") }; TextButton(onClick = { buying = null }) { Text("Cancel") } } })
        }
    }
}

// ------------------------------------------------------------------------------------------------ logbook, finances
@Composable
fun LogbookScreen(go: (Dest) -> Unit) {
    Loader("logbook", load = { it.get("logbook") }) { l ->
        ScreenColumn {
            Title("Logbook")
            Muted(l.str("totals"))
            l.list("flights").forEach { f ->
                InfoCard(onClick = { go(Dest.LogFlight(f.int("id"))) }) {
                    Row2(f.str("route"), f.str("net"), f.str("net_tone"))
                    Muted("${f.str("date")}  |  ${f.str("aircraft")}")
                    Muted("${f.str("air")}  |  ${f.str("distance")}  |  landing ${f.str("landing")}  |  score ${f.str("score")}  |  ${f.str("result")}")
                }
            }
        }
    }
}

@Composable
fun LogFlightScreen(id: Int) {
    Loader("logbook/$id", live = false, load = { it.get("logbook/$id") }) { f ->
        ScreenColumn {
            Title(f.str("title"))
            Muted(f.str("contract")); Muted(f.str("sim_title"))
            InfoCard { f.strings("stats").forEach { Text(it) }; Muted(f.str("money")) }
            if (f.str("debrief").isNotBlank()) { Heading("Debrief"); Text(f.str("debrief")) }
            val events = f.strings("events")
            if (events.isNotEmpty()) { Heading("Events"); events.forEach { Muted(it) } }
        }
    }
}

@Composable
fun FinanceScreen() {
    Loader("finance", load = { it.get("finance") }) { f ->
        ScreenColumn {
            Title("Finances")
            Tiles(f.list("tiles"))
            Heading("Transactions")
            f.list("rows").forEach { r ->
                InfoCard {
                    Row2(r.str("description"), r.str("amount"), r.str("tone"))
                    Muted("${r.str("date")}  |  ${r.str("category")}  |  balance ${r.str("balance")}")
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------ training
@Composable
fun TrainingScreen() {
    val act = rememberAct()
    Loader("training", load = { it.get("training") }) { t ->
        ScreenColumn {
            Title("Training and licences")
            Heading("Certificates")
            t.list("certs").forEach { c ->
                InfoCard {
                    Row2(c.str("label"), if (c.bool("valid")) "Valid" else "Expired", if (c.bool("valid")) "good" else "bad")
                    Muted("Expires ${c.str("expires")}  (${c.int("days_left")} days)")
                    Button(enabled = c.bool("can_renew") && c.bool("affordable"), onClick = { act { it.post("training/renew", jsonOf("kind" to c.str("kind"))) } }) { Text("Renew ${c.str("fee")}") }
                }
            }
            Heading("Courses")
            t.list("courses").forEach { c ->
                InfoCard {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(c.str("name"), style = MaterialTheme.typography.titleSmall)
                        if (c.bool("held")) Pill("Held", "good") else if (c.bool("in_training")) Pill("Until ${c.str("finishes")}", "accent")
                    }
                    Muted(c.str("blurb")); Muted("${c.str("fee")}  |  ${c.str("duration")}")
                    if (c.str("problem").isNotEmpty()) Text(c.str("problem"), color = toneColor("warn"), style = MaterialTheme.typography.bodySmall)
                    if (!c.bool("held") && !c.bool("in_training")) Button(enabled = c.bool("can_start"), onClick = { act { it.post("training/start", jsonOf("course_id" to c.str("id"))) } }) { Text("Start course") }
                }
            }
            val bills = t.list("bills")
            if (bills.isNotEmpty()) {
                Heading("Recurring costs")
                bills.forEach { b -> InfoCard { b.entries.forEach { (k, v) -> Muted("$k: ${v.toString().trim('"')}") } } }
                Muted("Total ${t.str("total")}. Next due ${t.str("next_due")}. ${t.str("runway")}")
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------ settings
@Composable
fun SettingsScreen(onForget: () -> Unit) {
    val act = rememberAct()
    Loader("settings", load = { it.get("settings") }) { s ->
        var simbrief by remember(s.str("simbrief_user")) { mutableStateOf(s.str("simbrief_user")) }
        ScreenColumn {
            Title("Settings")
            Heading("Voice")
            Choice("Where speech plays", listOf("phone" to "On this phone", "pc" to "On the PC"), s.str("speech_output")) { v -> act { it.post("settings", jsonOf("speech_output" to v)) } }
            Switch2("Speak replies aloud", s.bool("auto_speak_replies")) { v -> act { it.post("settings", jsonOf("auto_speak_replies" to v)) } }
            Switch2("Co-pilot call-outs in flight", s.bool("copilot_callouts")) { v -> act { it.post("settings", jsonOf("copilot_callouts" to v)) } }
            s.obj("voice")?.let { v -> Muted("PC voice output ${if (v.bool("speech_out")) "ready" else "unavailable"}; speech recognition ${if (v.bool("speech_in")) "ready" else "unavailable"}. ${v.str("note")}") }
            Heading("Units")
            Choice("Distance", listOf("nm" to "Nautical miles", "km" to "Kilometres"), s.str("units_distance")) { v -> act { it.post("settings", jsonOf("units_distance" to v)) } }
            Choice("Weight", listOf("lb" to "Pounds", "kg" to "Kilograms"), s.str("units_weight")) { v -> act { it.post("settings", jsonOf("units_weight" to v)) } }
            Heading("Flight plans")
            Switch2("Load fuel and payload into the sim automatically", s.bool("auto_sync_loadout")) { v -> act { it.post("settings", jsonOf("auto_sync_loadout" to v)) } }
            OutlinedTextField(simbrief, { simbrief = it }, label = { Text("SimBrief username") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedButton(enabled = simbrief != s.str("simbrief_user"), onClick = { act { it.post("settings", jsonOf("simbrief_user" to simbrief)) } }) { Text("Save SimBrief name") }
            Heading("This phone")
            Muted("App version ${BuildInfo.version}; PC version ${s.str("version")}")
            s.obj("update")?.let { Text("PC update available: ${it.str("version")}", color = toneColor("accent")) }
            Muted(s.str("note"))
            OutlinedButton(onClick = onForget) { Text("Forget this PC") }
        }
    }
}

@Composable
private fun Switch2(label: String, on: Boolean, change: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f)); Switch(on, change)
    }
}

@Composable
private fun Choice(title: String, options: List<Pair<String, String>>, selected: String, change: (String) -> Unit) {
    Text(title, style = MaterialTheme.typography.titleSmall)
    options.forEach { (id, label) ->
        Row(Modifier.fillMaxWidth().selectable(id == selected, role = Role.RadioButton) { change(id) }, verticalAlignment = Alignment.CenterVertically) {
            RadioButton(id == selected, onClick = null); Text(label, Modifier.padding(start = 12.dp, top = 6.dp, bottom = 6.dp))
        }
    }
}

object BuildInfo { val version: String get() = BuildConfig.VERSION_NAME }
