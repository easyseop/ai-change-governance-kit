class Job {
    void run(String cmd) throws Exception {
        Runtime.getRuntime().exec(cmd);
    }
}
