package tbot.modules.sys.service.impl;

import org.springframework.stereotype.Service;

import lombok.AllArgsConstructor;
import tbot.modules.security.oauth2.TokenGenerator;
import tbot.modules.sys.service.TokenService;

@AllArgsConstructor
@Service
public class TokenServiceImpl implements TokenService {

    @Override
    public String createToken(long userId) {
        // Generate Onetoken
        String token = TokenGenerator.generateValue();
        return token;
    }
}
