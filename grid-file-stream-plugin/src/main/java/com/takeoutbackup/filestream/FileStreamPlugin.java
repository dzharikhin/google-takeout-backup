package com.takeoutbackup.filestream;

import org.openqa.selenium.grid.config.Config;
import org.openqa.selenium.grid.data.SessionClosedEvent;
import org.openqa.selenium.grid.data.SessionCreatedEvent;
import org.openqa.selenium.grid.node.NodeCommandInterceptor;
import org.openqa.selenium.events.EventBus;
import org.openqa.selenium.remote.SessionId;
import org.openqa.selenium.remote.http.HttpRequest;
import org.openqa.selenium.remote.http.HttpResponse;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.logging.Level;
import java.util.logging.Logger;

public class FileStreamPlugin implements NodeCommandInterceptor {
    private static final Logger LOGGER = Logger.getLogger(FileStreamPlugin.class.getName());

    private final ConcurrentHashMap<String, Boolean> sessionDirs = new ConcurrentHashMap<>();
    private final ExecutorService executor;
    private ServerSocket serverSocket;
    private volatile boolean running;

    public FileStreamPlugin() {
        this.executor = Executors.newCachedThreadPool();
    }

    @Override
    public boolean isEnabled(Config config) {
        return true;
    }

    @Override
    public void initialize(Config config, EventBus bus) {
        String pskHex = System.getProperty("filestream.key", "");
        if (pskHex.isEmpty()) {
            LOGGER.severe("FILE_STREAM_KEY not set via -Dfilestream.key, file-stream endpoint will not be available");
            return;
        }

        String portStr = System.getProperty("filestream.port", "4445");
        String downloadsDir = System.getProperty("filestream.dir", "/app/grid-downloads");

        try {
            byte[] psk = hexToBytes(pskHex);

            bus.addListener(SessionCreatedEvent.listener(data -> {
                String sessionId = data.getSessionId().toString();
                sessionDirs.put(sessionId, Boolean.TRUE);
                LOGGER.info("Session " + sessionId + " added to file-stream known sessions");
            }));

            bus.addListener(SessionClosedEvent.listener(data -> {
                sessionDirs.remove(data.getSessionId().toString());
                LOGGER.info("Session " + data.getSessionId() + " removed from file-stream known sessions");
            }));

            int port = Integer.parseInt(portStr);
            serverSocket = new ServerSocket();
            serverSocket.setReuseAddress(true);
            serverSocket.bind(new InetSocketAddress(port));

            StreamHandler handler = new StreamHandler(sessionDirs, downloadsDir);
            running = true;
            executor.submit(() -> acceptLoop(handler));

            LOGGER.info("File-stream plugin started on port " + port + ", PSK: " + pskHex.substring(0, 8) + "...");

        } catch (Exception e) {
            LOGGER.log(Level.SEVERE, "Failed to initialize file-stream plugin", e);
            throw new RuntimeException(e);
        }
    }

    private void acceptLoop(StreamHandler handler) {
        while (running && !serverSocket.isClosed()) {
            Socket client;
            try {
                client = serverSocket.accept();
            } catch (IOException e) {
                if (running && !serverSocket.isClosed()) {
                    LOGGER.log(Level.WARNING, "Accept failed: " + e.getMessage(), e);
                }
                continue;
            }
            executor.submit(() -> {
                try {
                    client.setSoTimeout(120_000);
                    handler.handle(client);
                } catch (Exception e) {
                    LOGGER.log(Level.FINE, "Error handling file-stream connection: " + e.getMessage(), e);
                } finally {
                    try {
                        client.close();
                    } catch (IOException ignored) {
                    }
                }
            });
        }
    }

    @Override
    public HttpResponse intercept(SessionId id, HttpRequest req, java.util.concurrent.Callable<HttpResponse> next)
            throws Exception {
        return next.call();
    }

    @Override
    public void close() throws IOException {
        running = false;
        if (serverSocket != null && !serverSocket.isClosed()) {
            serverSocket.close();
            LOGGER.info("File-stream plugin stopped");
        }
        executor.shutdown();
    }

    private byte[] hexToBytes(String hex) {
        if (hex.length() % 2 != 0) {
            throw new IllegalArgumentException("Hex string must have even length");
        }
        byte[] bytes = new byte[hex.length() / 2];
        for (int i = 0; i < hex.length(); i += 2) {
            bytes[i / 2] = (byte) ((Character.digit(hex.charAt(i), 16) << 4)
                    + Character.digit(hex.charAt(i + 1), 16));
        }
        return bytes;
    }
}
