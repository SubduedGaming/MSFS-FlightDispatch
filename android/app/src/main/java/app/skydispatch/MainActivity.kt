package app.skydispatch

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.launch
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonPrimitive

class MainActivity : ComponentActivity() {
    /** A `skydispatch://pair` link that opened the app (also when it was already running). */
    private var link by mutableStateOf<PairingLink?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = Store(applicationContext)
        link = intent?.dataString?.let { PairingLink.parse(it) }
        Notify.attach(applicationContext)
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { App(store, link) } } }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { PairingLink.parse(it) }?.let { link = it }
    }

    override fun onStart() { super.onStart(); Notify.appVisible = true }
    override fun onStop() { Notify.appVisible = false; super.onStop() }
}

/** What the app knows about the PC right now. */
private sealed interface Gate {
    data object Loading : Gate
    data class Problem(val message: String) : Gate
    data object NeedsCareer : Gate
    data object Ready : Gate
}

@Composable
fun App(store: Store, link: PairingLink?) {
    val context = LocalContext.current
    var conn by remember { mutableStateOf<Connection?>(null) }
    var loaded by remember { mutableStateOf(false) }
    var notice by remember { mutableStateOf<String?>(null) }
    var gate by remember { mutableStateOf<Gate>(Gate.Loading) }
    var reload by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    val revoked by Hub.revoked.collectAsState()

    LaunchedEffect(Unit) { conn = store.load(); loaded = true }
    LaunchedEffect(conn) { conn?.let { Hub.start(context, it) } ?: Hub.stop() }
    LaunchedEffect(revoked) {
        if (revoked) { store.clear(); conn = null; notice = "This phone was removed on the PC. Pair it again." }
    }
    LaunchedEffect(conn, reload) {
        val c = conn ?: return@LaunchedEffect
        gate = Gate.Loading
        gate = try {
            if (Api(c.baseUrl, c.token).state()["has_career"]?.jsonPrimitive?.boolean == true) Gate.Ready else Gate.NeedsCareer
        } catch (e: ApiException) {
            if (e.status == 401) {
                store.clear(); conn = null; notice = "This phone was removed on the PC. Pair it again."
                Gate.Loading
            } else Gate.Problem(e.message ?: "Could not reach the PC.")
        }
    }
    if (!loaded) return

    val c = conn
    if (c == null) {
        PairScreen(link, notice) { saved -> scope.launch { store.save(saved); notice = null; conn = saved } }
        return
    }
    val forget = {
        scope.launch {
            try { Hub.api?.post("unpair") } catch (e: ApiException) { /* the PC may already have removed it */ }
            FlightService.set(context, false)
            store.clear(); conn = null
        }
        Unit
    }
    when (val g = gate) {
        Gate.Loading -> Centered { CircularProgressIndicator() }
        is Gate.Problem -> Centered {
            Text(g.message)
            Button(onClick = { reload++ }) { Text("Try again") }
            OutlinedButton(onClick = { forget() }) { Text("Forget this PC") }
        }
        Gate.NeedsCareer -> CareerScreen(c, onForget = { forget() }) { reload++ }
        Gate.Ready -> Shell(store, onForget = { forget() })
    }
}

sealed interface Dest {
    val title: String
    data class Chat(val id: String) : Dest { override val title = "Messages" }
    data class Market(val id: Int) : Dest { override val title = "Job" }
    data class Employer(val id: String) : Dest { override val title = "Company" }
    data class LogFlight(val id: Int) : Dest { override val title = "Flight" }
    data object Hangar : Dest { override val title = "Hangar" }
    data object Dealer : Dest { override val title = "Dealer" }
    data object Logbook : Dest { override val title = "Logbook" }
    data object Finance : Dest { override val title = "Finances" }
    data object Training : Dest { override val title = "Training" }
    data object Settings : Dest { override val title = "Settings" }
}

private val TABS = listOf("Home", "Flight", "Jobs", "Messages", "More")

@Composable
fun Shell(store: Store, onForget: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var stack by remember { mutableStateOf(listOf<Dest>()) }
    val snack = remember { SnackbarHostState() }
    val online by Hub.online.collectAsState()
    val version by Hub.version.collectAsState()
    var askSpeech by remember { mutableStateOf(false) }

    val askNotifications = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33 && !Notify.canPost(context)) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        if (!store.flag("asked_speech")) askSpeech = true
        Hub.toasts.collect { snack.showSnackbar(it.text) }
    }
    // Keep the connection alive in the background while a job is active.
    LaunchedEffect(version) {
        try { FlightService.set(context, Hub.api?.state()?.get("has_job")?.jsonPrimitive?.boolean == true) } catch (e: ApiException) { }
    }
    BackHandler(stack.isNotEmpty()) { stack = stack.dropLast(1) }

    val go = { d: Dest -> stack = stack + d }
    val typing = WindowInsets.ime.getBottom(androidx.compose.ui.platform.LocalDensity.current) > 0
    Scaffold(
        snackbarHost = { SnackbarHost(snack) },
        bottomBar = {
            if (!typing) NavigationBar {
                val icons = listOf(Icons.Filled.Home, Icons.Filled.Send, Icons.Filled.Search, Icons.Filled.Email, Icons.Filled.Menu)
                TABS.forEachIndexed { i, label ->
                    NavigationBarItem(selected = stack.isEmpty() && tab == i, onClick = { tab = i; stack = emptyList() },
                        icon = { Icon(icons[i], label) }, label = { Text(label) })
                }
            }
        },
    ) { pad ->
        Column(Modifier.padding(pad).imePadding()) {
            if (!online) Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth()) {
                Text("Reconnecting to your PC...", Modifier.padding(8.dp), style = MaterialTheme.typography.bodySmall)
            }
            val top = stack.lastOrNull()
            if (top != null) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) { TextButton(onClick = { stack = stack.dropLast(1) }) { Text("< ${top.title}") } }
                when (top) {
                    is Dest.Chat -> ChatScreen(top.id)
                    is Dest.Market -> MarketDetail(top.id) { stack = stack.dropLast(1) }
                    is Dest.Employer -> EmployerDetail(top.id, go)
                    is Dest.LogFlight -> LogFlightScreen(top.id)
                    Dest.Hangar -> HangarScreen()
                    Dest.Dealer -> DealerScreen()
                    Dest.Logbook -> LogbookScreen(go)
                    Dest.Finance -> FinanceScreen()
                    Dest.Training -> TrainingScreen()
                    Dest.Settings -> SettingsScreen(onForget)
                }
            } else when (tab) {
                0 -> HomeScreen(go) { tab = 1 }
                1 -> FlightScreen { tab = 2 }
                2 -> JobsScreen(go)
                3 -> MessagesScreen(go)
                else -> MoreScreen(go, BuildInfo.version)
            }
        }
    }
    if (askSpeech) AlertDialog(
        onDismissRequest = { },
        title = { Text("Hear your dispatcher on this phone?") },
        text = { Text("Replies can be spoken through this phone instead of the PC's speakers. You can change this later in Settings.") },
        confirmButton = {
            TextButton(onClick = {
                askSpeech = false
                scope.launch {
                    store.setFlag("asked_speech")
                    try { Hub.api?.post("settings", jsonOf("speech_output" to "phone")) } catch (e: ApiException) { Hub.toast("bad", e.message ?: "") }
                }
            }) { Text("Use this phone") }
        },
        dismissButton = { TextButton(onClick = { askSpeech = false; scope.launch { store.setFlag("asked_speech") } }) { Text("Keep on the PC") } },
    )
}

@Composable
fun PairScreen(link: PairingLink?, notice: String?, onPaired: (Connection) -> Unit) {
    var host by remember(link) { mutableStateOf(link?.host ?: "") }
    var port by remember(link) { mutableStateOf((link?.port ?: 8766).toString()) }
    var code by remember(link) { mutableStateOf(link?.code ?: "") }
    var error by remember { mutableStateOf(notice) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    fun connect(h: String, p: Int, c: String) {
        busy = true
        error = null
        scope.launch {
            try {
                onPaired(pairWith(h, p, c, Build.MODEL ?: "Android phone"))
            } catch (e: ApiException) {
                error = e.message
            } finally {
                busy = false
            }
        }
    }

    Column(Modifier.padding(24.dp).statusBarsPadding().imePadding(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Connect to your PC", style = MaterialTheme.typography.headlineSmall)
        Text("On the PC, open SkyDispatch and go to the Phones tab. Scan the QR code shown there, or type the address and pairing code.")
        Button(enabled = !busy, modifier = Modifier.fillMaxWidth(), onClick = {
            val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).enableAutoZoom().build()
            GmsBarcodeScanning.getClient(context, options).startScan()
                .addOnSuccessListener { barcode ->
                    val l = barcode.rawValue?.let { PairingLink.parse(it) }
                    if (l == null) error = "That QR code is not from SkyDispatch."
                    else { host = l.host; port = l.port.toString(); code = l.code; connect(l.host, l.port, l.code) }
                }
                .addOnFailureListener { error = "Could not open the camera scanner. Type the address and code instead." }
        }) { Text("Scan QR code") }
        OutlinedTextField(host, { host = it }, label = { Text("PC address (e.g. 192.168.1.20)") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(port, { port = it.filter(Char::isDigit) }, label = { Text("Port") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(code, { code = it }, label = { Text("Pairing code") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        OutlinedButton(enabled = !busy && host.isNotBlank() && code.isNotBlank(),
            onClick = { connect(host, port.toIntOrNull() ?: 0, code) }) {
            Text(if (busy) "Connecting..." else "Connect")
        }
    }
}
