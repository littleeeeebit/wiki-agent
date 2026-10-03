package dev.wikiagent.mobile;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;
import java.util.Optional;
import java.util.regex.Pattern;

final class PairingLink {
    private static final Pattern SECRET = Pattern.compile("pair=[A-Za-z0-9_-]{16,128}");

    private PairingLink() {}

    static Optional<URI> parse(String raw) {
        try {
            URI uri = new URI(raw == null ? "" : raw.trim());
            String path = uri.getPath();
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                    || uri.getUserInfo() != null || uri.getQuery() != null
                    || !(path == null || path.isEmpty() || "/".equals(path))
                    || uri.getRawFragment() == null || !SECRET.matcher(uri.getRawFragment()).matches()) {
                return Optional.empty();
            }
            return Optional.of(uri);
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return Optional.empty();
        }
    }

    static Optional<URI> savedOrigin(String raw) {
        try {
            URI uri = new URI(raw == null ? "" : raw.trim());
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                    || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                    || !"/".equals(uri.getPath())) {
                return Optional.empty();
            }
            return Optional.of(uri);
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return Optional.empty();
        }
    }

    static URI origin(URI pairing) {
        try {
            return new URI("https", null, pairing.getHost().toLowerCase(Locale.ROOT), pairing.getPort(),
                    "/", null, null);
        } catch (URISyntaxException impossible) {
            throw new IllegalArgumentException(impossible);
        }
    }

    static boolean sameOrigin(URI origin, String candidate) {
        try {
            URI uri = new URI(candidate);
            return "https".equalsIgnoreCase(uri.getScheme())
                    && origin.getHost().equalsIgnoreCase(uri.getHost())
                    && effectivePort(origin) == effectivePort(uri);
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return false;
        }
    }

    private static int effectivePort(URI uri) {
        return uri.getPort() == -1 ? 443 : uri.getPort();
    }
}
