package com.takeoutbackup.grid;

import com.google.gson.JsonObject;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.logging.Logger;

public class CookieConverter {

    private static final Logger LOGGER = Logger.getLogger(CookieConverter.class.getName());

    public static JsonObject classicToBiDi(JsonObject classic) {
        JsonObject bidi = new JsonObject();

        if (classic.has("name")) bidi.add("name", classic.get("name"));
        if (classic.has("domain")) {
            bidi.add("domain", classic.get("domain"));
        }
        if (classic.has("path")) bidi.add("path", classic.get("path"));
        if (classic.has("expiry")) bidi.add("expiry", classic.get("expiry"));
        if (classic.has("size")) bidi.add("size", classic.get("size"));
        if (classic.has("httpOnly")) bidi.add("httpOnly", classic.get("httpOnly"));
        if (classic.has("secure")) bidi.add("secure", classic.get("secure"));

        if (classic.has("sameSite")) {
            String sameSite = classic.get("sameSite").getAsString();
            bidi.addProperty("sameSite", sameSite.toLowerCase());
        } else {
            bidi.addProperty("sameSite", "none");
        }

        if (classic.has("value")) {
            String value = classic.get("value").getAsString();
            JsonObject valueObj = new JsonObject();
            if (StandardCharsets.UTF_8.newEncoder().canEncode(value)) {
                valueObj.addProperty("type", "string");
                valueObj.addProperty("value", value);
            } else {
                valueObj.addProperty("type", "base64");
                valueObj.addProperty("value", Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.ISO_8859_1)));
            }
            bidi.add("value", valueObj);
        }

        return bidi;
    }

    public static JsonObject biDiToClassic(JsonObject bidi) {
        JsonObject classic = new JsonObject();

        if (bidi.has("name")) classic.add("name", bidi.get("name"));
        if (bidi.has("domain")) classic.add("domain", bidi.get("domain"));
        if (bidi.has("path")) classic.add("path", bidi.get("path"));
        if (bidi.has("expiry")) classic.add("expiry", bidi.get("expiry"));
        if (bidi.has("httpOnly")) classic.add("httpOnly", bidi.get("httpOnly"));
        if (bidi.has("secure")) classic.add("secure", bidi.get("secure"));

        if (bidi.has("sameSite")) {
            String sameSite = bidi.get("sameSite").getAsString();
            classic.addProperty("sameSite", capitalize(sameSite));
        }

        if (bidi.has("value")) {
            JsonObject valueObj = bidi.getAsJsonObject("value");
            if (valueObj != null) {
                String type = valueObj.has("type") ? valueObj.get("type").getAsString() : "string";
                String value = valueObj.has("value") ? valueObj.get("value").getAsString() : "";
                if ("base64".equals(type)) {
                    value = new String(Base64.getDecoder().decode(value), StandardCharsets.ISO_8859_1);
                }
                classic.addProperty("value", value);
            }
        }

        return classic;
    }

    private static String capitalize(String s) {
        if (s == null || s.isEmpty()) return s;
        return s.substring(0, 1).toUpperCase() + s.substring(1).toLowerCase();
    }
}