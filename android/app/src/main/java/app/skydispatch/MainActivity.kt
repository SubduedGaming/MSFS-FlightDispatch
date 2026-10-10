package app.skydispatch

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = Store(applicationContext)
        val link = intent?.dataString?.let { PairingLink.parse(it) }
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { App(store, link) } } }
    }
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
    var conn by remember { mutableStateOf<Connection?>(null) }
    var loaded by remember { mutableStateOf(false) }
    var notice by remember { mutableStateOf<String?>(null) }
    var gate by remember { mutableStateOf<Gate>(Gate.Loading) }
    var reload by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) { conn = store.load(); loaded = true }
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
    val forget = { scope.launch { store.clear(); conn = null } }
    when (val g = gate) {
        Gate.Loading -> Centered { CircularProgressIndicator() }
        is Gate.Problem -> Centered {
            Text(g.message)
            Button(onClick = { reload++ }) { Text("Try again") }
            OutlinedButton(onClick = { forget() }) { Text("Forget this PC") }
        }
        Gate.NeedsCareer -> CareerScreen(c, onForget = { forget() }) { reload++ }
        Gate.Ready -> ConnectedScreen(c) { forget() }
    }
}

@Composable
fun Centered(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.spacedBy(12.dp, androidx.compose.ui.Alignment.CenterVertically),
        horizontalAlignment = androidx.compose.ui.Alignment.CenterHorizontally, content = content)
}

@Composable
fun PairScreen(link: PairingLink?, notice: String?, onPaired: (Connection) -> Unit) {
    var host by remember { mutableStateOf(link?.host ?: "") }
    var port by remember { mutableStateOf((link?.port ?: 8766).toString()) }
    var code by remember { mutableStateOf(link?.code ?: "") }
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

@Composable
fun ConnectedScreen(c: Connection, onForget: () -> Unit) {
    var text by remember { mutableStateOf("Checking...") }
    LaunchedEffect(c) {
        text = try {
            "Connected to ${c.baseUrl}\n\nState: ${Api(c.baseUrl, c.token).state()}"
        } catch (e: ApiException) {
            e.message ?: "Error"
        }
    }
    Column(Modifier.padding(24.dp).statusBarsPadding(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("SkyDispatch", style = MaterialTheme.typography.headlineSmall)
        Text(text)
        OutlinedButton(onClick = onForget) { Text("Forget this PC") }
    }
}
