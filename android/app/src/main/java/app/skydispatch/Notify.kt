package app.skydispatch

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/** Notifications for new dispatcher messages while the app is not on screen. */
object Notify {
    const val CH_FLIGHT = "flight"
    const val CH_MESSAGES = "messages"

    /** True between the activity's onStart and onStop. */
    @Volatile var appVisible = false

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val lastSeen = HashMap<String, Int>()
    private var attached = false

    fun ensureChannels(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_FLIGHT, "Flight in progress", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CH_MESSAGES, "Dispatcher messages", NotificationManager.IMPORTANCE_DEFAULT))
    }

    fun canPost(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || ctx.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** Start listening for `thread` events. Safe to call more than once. */
    fun attach(ctx: Context) {
        if (attached) return
        attached = true
        val app = ctx.applicationContext
        ensureChannels(app)
        scope.launch {
            Hub.events.collect { ev ->
                if (ev.name != "thread" || appVisible || !canPost(app)) return@collect
                val tid = ev.data.str("thread")
                if (tid.isEmpty() || tid == "copilot") return@collect
                val api = Hub.api ?: return@collect
                try {
                    val msgs = api.get("thread/$tid").list("messages")
                    val last = msgs.lastOrNull() ?: return@collect
                    val id = last.int("id")
                    if (last.str("role") != "assistant" || id <= (lastSeen[tid] ?: -1)) return@collect
                    lastSeen[tid] = id
                    post(app, tid, api.get("thread/$tid").str("name", "Dispatch"), last.str("content").ifBlank { "Flight offer" })
                } catch (e: ApiException) { /* try again on the next event */ }
            }
        }
    }

    private fun post(ctx: Context, tid: String, title: String, text: String) {
        val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = Notification.Builder(ctx, CH_MESSAGES).setSmallIcon(android.R.drawable.ic_dialog_email)
            .setContentTitle(title).setContentText(text.take(200)).setStyle(Notification.BigTextStyle().bigText(text.take(500)))
            .setContentIntent(open).setAutoCancel(true).build()
        ctx.getSystemService(NotificationManager::class.java).notify(tid.hashCode(), n)
    }
}

/** Keeps the app alive (and so the event stream open) while a flight is in progress. */
class FlightService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Notify.ensureChannels(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, Notify.CH_FLIGHT).setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentTitle("SkyDispatch").setContentText("Flight in progress, connected to your PC")
            .setContentIntent(open).setOngoing(true).build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(1, n)
        return START_NOT_STICKY
    }

    companion object {
        fun set(ctx: Context, on: Boolean) {
            val i = Intent(ctx, FlightService::class.java)
            try {
                if (on) ctx.startForegroundService(i) else ctx.stopService(i)
            } catch (e: Exception) { /* Android refused to start it from the background; the app still works */ }
        }
    }
}
