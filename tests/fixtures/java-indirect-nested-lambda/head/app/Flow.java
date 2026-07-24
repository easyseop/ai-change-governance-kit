interface Port { void run(); }
interface Inner { void go(); }
class Ledger { void settle() { int value = 2; } }
class Flow {
    Port port;
    Inner in;
    void sink() { port.run(); }
    void wire() {
        port = () -> {
            in = () -> new Ledger().settle();
            in.go();
        };
    }
}
