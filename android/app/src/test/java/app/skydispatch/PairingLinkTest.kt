package app.skydispatch

import org.junit.Assert.*
import org.junit.Test

class PairingLinkTest {
    @Test fun parsesTheLinkFromTheQr() {
        val l = PairingLink.parse("skydispatch://pair?host=192.168.1.20&port=8766&code=ABCD2345")
        assertEquals(PairingLink("192.168.1.20", 8766, "ABCD2345"), l)
    }

    @Test fun rejectsOtherLinksAndBrokenOnes() {
        assertNull(PairingLink.parse("https://example.com/pair?host=1.2.3.4&port=1&code=x"))
        assertNull(PairingLink.parse("skydispatch://pair?host=192.168.1.20&port=99999&code=x"))
        assertNull(PairingLink.parse("skydispatch://pair?host=192.168.1.20&port=8766"))
        assertNull(PairingLink.parse("not a url"))
    }

    @Test fun onlyHomeNetworkAddressesAreAllowed() {
        for (ok in listOf("192.168.1.20", "10.0.2.2", "172.16.0.5", "172.31.255.1", "pc.local", "127.0.0.1"))
            assertTrue(ok, PairingLink.isPrivateHost(ok))
        for (bad in listOf("8.8.8.8", "172.32.0.1", "example.com", "192.169.1.1", "300.1.1.1"))
            assertFalse(bad, PairingLink.isPrivateHost(bad))
    }
}
