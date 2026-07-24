interface Port { void run(); }
class Ledger { void settle() { int value = 2; } }
class Flow {
    Port port;
    void sink() { port.run(); }
    void wire() { port = new Port() { public void run() { new Ledger().settle(); } }; }
    void lambdaWire() { port = () -> new Ledger().settle(); }
}
