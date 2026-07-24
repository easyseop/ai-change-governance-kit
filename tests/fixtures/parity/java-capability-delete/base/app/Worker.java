class Worker {
    void run(String cmd) throws Exception {
        Runtime.getRuntime().exec(cmd);
    }
}
