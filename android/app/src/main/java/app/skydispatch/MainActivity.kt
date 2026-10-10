package app.skydispatch

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val store = Store(applicationContext)
        val link = intent?.dataString?.let { PairingLink.parse(it) }
        setContent { MaterialTheme { Surface(Modifier.fillMaxSize()) { App(store, link) } } }
    }
}

@Composable
fun App(store: Store, link: PairingLink?) {
    var conn by remember { mutableStateOf<Connection?>(null) }
    var loaded by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { conn = store.load(); loaded = true }
    if (!loaded) return
    val c = conn
    if (c == null) PairScreen(link) { saved -> scope.launch { store.save(saved); conn = saved } }
    else ConnectedScreen(c) { scope.launch { store.clear(); conn = null } }
}

@Composable
fun PairScreen(link: PairingLink?, onPaired: (Connection) -> Unit) {
    var host by remember { mutableStateOf(link?.host ?: "") }
    var port by remember { mutableStateOf((link?.port ?: 8766).toString()) }
    var code by remember { mutableStateOf(link?.code ?: "") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(Modifier.padding(24.dp).statusBarsPadding(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Connect to your PC", style = MaterialTheme.typography.headlineSmall)
        Text("On the PC, open SkyDispatch and go to the Phones tab. Type the address and pairing code shown there.")
        OutlinedTextField(host, { host = it }, label = { Text("PC address (e.g. 192.168.1.20)") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(port, { port = it.filter(Char::isDigit) }, label = { Text("Port") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(code, { code = it }, label = { Text("Pairing code") },
            singleLine = true, modifier = Modifier.fillMaxWidth())
        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        Button(enabled = !busy && host.isNotBlank() && code.isNotBlank(), onClick = {
            val p = port.toIntOrNull()
            if (p == null || p !in 1..65535) { error = "That port is not valid."; return@Button }
            if (!PairingLink.isPrivateHost(host)) {
                error = "Use the PC's home-network address (it usually starts with 192.168 or 10)."
                return@Button
            }
            busy = true
            error = null
            scope.launch {
                try {
                    val base = "http://${host.trim()}:$p"
                    val ping = Api(base).ping()
                    if (ping.app != "SkyDispatch") throw ApiException("That is not a SkyDispatch server.")
                    val paired = Api(base).pair(code.trim(), Build.MODEL ?: "Android phone")
                    onPaired(Connection(base, paired.token))
                } catch (e: ApiException) {
                    error = e.message
                } finally {
                    busy = false
                }
            }
        }) { Text(if (busy) "Connecting..." else "Connect") }
    }
}

@Composable
fun ConnectedScreen(c: Connection, onForget: () -> Unit) {
    var text by remember { mutableStateOf("Checking...") }
    LaunchedEffect(c) {
        text = try {
            val s = Api(c.baseUrl, c.token).state()
            "Connected to ${c.baseUrl}\n\nState: $s"
        } catch (e: ApiException) {
            if (e.status == 401) "This phone was removed on the PC. Forget it and pair again." else e.message ?: "Error"
        }
    }
    Column(Modifier.padding(24.dp).statusBarsPadding(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("SkyDispatch", style = MaterialTheme.typography.headlineSmall)
        Text(text)
        OutlinedButton(onClick = onForget) { Text("Forget this PC") }
    }
}
