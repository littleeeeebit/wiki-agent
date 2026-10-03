package dev.wikiagent.mobile;

import android.content.pm.ActivityInfo;
import org.junit.Test;
import static org.junit.Assert.*;

public final class ScreenModeTest {
    @Test public void explicitChoiceOwnsScreenAndLayout() {
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT, ScreenMode.PORTRAIT.orientation);
        assertEquals("portrait", ScreenMode.PORTRAIT.layout);
        assertEquals(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE, ScreenMode.LANDSCAPE.orientation);
        assertEquals("landscape", ScreenMode.LANDSCAPE.layout);
        for (ScreenMode mode : ScreenMode.values()) assertSame(mode, ScreenMode.stored(mode.layout));
    }

    @Test public void unknownPreferenceDefaultsToPortraitWithoutSensors() {
        assertSame(ScreenMode.PORTRAIT, ScreenMode.stored(null));
        assertSame(ScreenMode.PORTRAIT, ScreenMode.stored("auto"));
    }
}
