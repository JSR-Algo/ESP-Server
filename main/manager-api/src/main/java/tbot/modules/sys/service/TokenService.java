package tbot.modules.sys.service;

public interface TokenService {
    /**
     * Generatetoken
     *
     * @param userId
     * @return
     */
    String createToken(long userId);
}
