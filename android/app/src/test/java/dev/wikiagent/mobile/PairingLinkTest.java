package dev.wikiagent.mobile;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.net.URI;

import org.junit.Test;

public final class PairingLinkTest {
    private static final String SECRET = "abcdefghijklmnop";

    @Test
    public void acceptsOnlyRootHttpsPairingLinks() {
        URI link = PairingLink.parse("https://Example.com/#pair=" + SECRET).orElseThrow();
        assertEquals("https://example.com/", PairingLink.origin(link).toString());
        assertTrue(PairingLink.sameOrigin(PairingLink.origin(link), "https://example.com/api/mobile/status"));

        for (String value : new String[] {
                "http://example.com/#pair=" + SECRET,
                "https://user@example.com/#pair=" + SECRET,
                "https://example.com/path#pair=" + SECRET,
                "https://example.com/?query=1#pair=" + SECRET,
                "https://example.com/#wrong=" + SECRET,
                "https://example.com/#pair=short",
        }) assertFalse(value, PairingLink.parse(value).isPresent());
    }

    @Test
    public void savedOriginsHaveNoCredentialsOrExtraUrlParts() {
        assertTrue(PairingLink.savedOrigin("https://example.com:8443/").isPresent());
        assertFalse(PairingLink.savedOrigin("https://example.com/#pair=" + SECRET).isPresent());
        assertFalse(PairingLink.savedOrigin("https://user@example.com/").isPresent());
        assertFalse(PairingLink.sameOrigin(URI.create("https://example.com/"), "https://evil.example/"));
    }
}
