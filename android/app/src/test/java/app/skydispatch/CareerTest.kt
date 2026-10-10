package app.skydispatch

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class CareerTest {
    // The shape career_options() returns on the PC.
    private val optionsJson = """{"starters":[{"id":"c152","name":"Cessna 152","installed":true},
        {"id":"c172","name":"Cessna 172","installed":false}],
        "experience":[{"id":"new","label":"New pilot"}],"difficulty":["relaxed","normal","realistic"],
        "currencies":["$","£"],"defaults":{"name":"","callsign":"SKY1","home":"EGLL","balance":25000,
        "difficulty":"normal","currency":"$","experience":"new","aircraft":"c172"}}"""

    @Test fun readsTheOptionsTheServerSends() = runBlocking {
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setBody(optionsJson))
            s.start()
            val o = Api("http://${s.hostName}:${s.port}", "tok").careerOptions()
            assertEquals(2, o.starters.size)
            assertFalse(o.starters[1].installed)
            assertEquals(25000.0, o.defaults.balance, 0.0)
            assertEquals("Bearer tok", s.takeRequest().getHeader("Authorization"))
        }
    }

    @Test fun formChecksWhatThePcWillCheck() {
        val ok = CareerForm("Sam", "egll", "sky1", "c152", "new", "normal", "$", "")
        assertNull(ok.problem())
        assertEquals("Enter your pilot name.", ok.copy(name = " ").problem())
        assertNotNull(ok.copy(home = "LO").problem())
        assertNotNull(ok.copy(aircraft = "").problem())
        assertNotNull(ok.copy(balance = "9999999").problem())
        val j = ok.toJson()
        assertEquals("EGLL", j["home"]!!.jsonPrimitive.content)
        assertFalse(j.containsKey("balance"))
    }

    @Test fun pairingSendsTheCodeAndKeepsTheToken() = runBlocking {
        MockWebServer().use { s ->
            s.enqueue(MockResponse().setBody("""{"app":"SkyDispatch","version":"2","api":1,"authed":false}"""))
            s.enqueue(MockResponse().setBody("""{"token":"T0K","device_id":"d1"}"""))
            s.start()
            val c = pairWith("127.0.0.1", s.port, " ABCD2345 ", "Pixel")
            assertEquals("T0K", c.token)
            s.takeRequest()
            val req = s.takeRequest()
            assertEquals("1", req.getHeader("X-SkyDispatch"))
            assertTrue(req.body.readUtf8().contains("ABCD2345"))
        }
    }

    @Test fun pairingRefusesAddressesOutsideTheHomeNetwork() = runBlocking {
        try { pairWith("8.8.8.8", 8766, "x", "Pixel"); fail() } catch (e: ApiException) { assertTrue(e.message!!.contains("home-network")) }
    }
}
