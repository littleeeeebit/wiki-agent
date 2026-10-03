package dev.wikiagent.mobile;

import android.content.pm.ActivityInfo;

/** One explicit choice owns both Activity orientation and the companion layout. */
enum ScreenMode {
    PORTRAIT("portrait", ActivityInfo.SCREEN_ORIENTATION_PORTRAIT),
    LANDSCAPE("landscape", ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);

    final String layout;
    final int orientation;

    ScreenMode(String layout, int orientation) {
        this.layout = layout;
        this.orientation = orientation;
    }

    static ScreenMode stored(String value) {
        return "landscape".equals(value) ? LANDSCAPE : PORTRAIT;
    }
}
