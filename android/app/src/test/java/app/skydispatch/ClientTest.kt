package app.skydispatch

import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

class ClientTest {
    @Test fun wavHeaderMatchesWhatThePcExpects() {
        val pcm = ByteArray(32_000) { 1 }                         // one second at 16 kHz, 16-bit mono
        val w = Recorder.wav(pcm)
        val b = ByteBuffer.wrap(w).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals(44 + pcm.size, w.size)
        assertEquals("RIFF", String(w, 0, 4)); assertEquals("WAVE", String(w, 8, 4)); assertEquals("data", String(w, 36, 4))
        assertEquals(1, b.getShort(20).toInt())                    // PCM
        assertEquals(1, b.getShort(22).toInt())                    // mono
        assertEquals(16_000, b.getInt(24))
        assertEquals(16, b.getShort(34).toInt())
        assertEquals(pcm.size, b.getInt(40))
    }

    @Test fun lenientReadersNeverCrashOnMissingOrNullFields() {
        val o = Json.parseToJsonElement("""{"a":"x","n":null,"i":45.0,"b":true,"l":[{"k":1},3],"s":["p",null]}""").jsonObject
        assertEquals("x", o.str("a")); assertEquals("", o.str("n")); assertEquals("", o.str("missing"))
        assertEquals(45, o.int("i")); assertEquals(0, o.int("n")); assertTrue(o.bool("b")); assertFalse(o.bool("missing"))
        assertEquals(1, o.list("l").size); assertEquals(listOf("p"), o.strings("s")); assertNull(o.obj("a"))
    }

    @Test fun eventStreamIsParsedIntoNamedEvents() = runBlocking {
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
                .setBody("retry: 3000\n\n: keepalive\n\nevent: state\ndata: {\"phase\":\"Climb\"}\n\nevent: speech\ndata: {\"id\":\"ab\",\n"
                    + "data: \"text\":\"hi\"}\n\n"))
            s.start()
            val events = Api("http://127.0.0.1:${s.port}", "tok").stream("viewer0123456789").toList()
            assertEquals(listOf("open", "state", "speech"), events.map { it.name })
            assertEquals("Climb", events[1].data.str("phase"))
            assertEquals("hi", events[2].data.str("text"))
            val req = s.takeRequest()
            assertEquals("Bearer tok", req.getHeader("Authorization"))
            assertTrue(req.path!!.contains("stream?v=viewer0123456789"))
        }
    }

    @Test fun errorsFromThePcAreShownAsTheyAre() = runBlocking {
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setResponseCode(409).setBody("""{"error":"No career yet."}"""))
            s.start()
            try { Api("http://127.0.0.1:${s.port}", "t").get("dashboard"); fail() }
            catch (e: ApiException) { assertEquals("No career yet.", e.message); assertEquals(409, e.status) }
        }
    }

    @Test fun readsAreRepeatedOnceButWritesAreNot() = runBlocking {
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.DISCONNECT_AFTER_REQUEST))
            s.enqueue(MockResponse().setBody("""{"ok":true}"""))
            s.start()
            assertTrue(Api("http://127.0.0.1:${s.port}", "t").get("state").bool("ok"))
            assertEquals(2, s.requestCount)
        }
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.DISCONNECT_AFTER_REQUEST))
            s.start()
            try { Api("http://127.0.0.1:${s.port}", "t").post("flight/end"); fail() } catch (e: ApiException) { }
            assertEquals(1, s.requestCount)
        }
    }
}
