package tbot.modules.security.config;

import java.text.SimpleDateFormat;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.converter.ByteArrayHttpMessageConverter;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.converter.ResourceHttpMessageConverter;
import org.springframework.http.converter.StringHttpMessageConverter;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.http.converter.support.AllEncompassingFormHttpMessageConverter;
import org.springframework.web.servlet.LocaleResolver;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;
import org.springframework.web.servlet.i18n.AcceptHeaderLocaleResolver;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.module.SimpleModule;
import com.fasterxml.jackson.databind.ser.std.ToStringSerializer;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.fasterxml.jackson.datatype.jsr310.deser.LocalDateDeserializer;
import com.fasterxml.jackson.datatype.jsr310.deser.LocalDateTimeDeserializer;
import com.fasterxml.jackson.datatype.jsr310.deser.LocalTimeDeserializer;
import com.fasterxml.jackson.datatype.jsr310.ser.LocalDateSerializer;
import com.fasterxml.jackson.datatype.jsr310.ser.LocalDateTimeSerializer;
import com.fasterxml.jackson.datatype.jsr310.ser.LocalTimeSerializer;

import tbot.common.utils.DateUtils;

@Configuration
public class WebMvcConfig implements WebMvcConfigurer {

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOriginPatterns("*")
                .allowCredentials(true)
                .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS")
                .maxAge(3600);
    }

    @Override
    public void configureMessageConverters(List<HttpMessageConverter<?>> converters) {
        // Special-purpose converter
        converters.add(new ByteArrayHttpMessageConverter());
        converters.add(new ResourceHttpMessageConverter());

        // Common converter
        converters.add(new StringHttpMessageConverter());
        converters.add(new AllEncompassingFormHttpMessageConverter());

        // JSON Converter
        converters.add(jackson2HttpMessageConverter());
    }

    @Bean
    public MappingJackson2HttpMessageConverter jackson2HttpMessageConverter() {
        MappingJackson2HttpMessageConverter converter = new MappingJackson2HttpMessageConverter();
        ObjectMapper mapper = new ObjectMapper();

        // Ignore unknown attributes
        mapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);

        // Set Timezone
        mapper.setTimeZone(TimeZone.getTimeZone("GMT+8"));

        // ConfigJava8Date time serialization
        JavaTimeModule javaTimeModule = new JavaTimeModule();
        javaTimeModule.addSerializer(java.time.LocalDateTime.class, new LocalDateTimeSerializer(
                java.time.format.DateTimeFormatter.ofPattern(DateUtils.DATE_TIME_PATTERN)));
        javaTimeModule.addSerializer(java.time.LocalDate.class, new LocalDateSerializer(
                java.time.format.DateTimeFormatter.ofPattern(DateUtils.DATE_PATTERN)));
        javaTimeModule.addSerializer(java.time.LocalTime.class,
                new LocalTimeSerializer(java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss")));
        javaTimeModule.addDeserializer(java.time.LocalDateTime.class, new LocalDateTimeDeserializer(
                java.time.format.DateTimeFormatter.ofPattern(DateUtils.DATE_TIME_PATTERN)));
        javaTimeModule.addDeserializer(java.time.LocalDate.class, new LocalDateDeserializer(
                java.time.format.DateTimeFormatter.ofPattern(DateUtils.DATE_PATTERN)));
        javaTimeModule.addDeserializer(java.time.LocalTime.class,
                new LocalTimeDeserializer(java.time.format.DateTimeFormatter.ofPattern("HH:mm:ss")));
        mapper.registerModule(javaTimeModule);

        // Configjava.util.DateSerialization and deserialization
        SimpleDateFormat dateFormat = new SimpleDateFormat(DateUtils.DATE_TIME_PATTERN);
        mapper.setDateFormat(dateFormat);

        // LongType convertStringType
        SimpleModule simpleModule = new SimpleModule();
        simpleModule.addSerializer(Long.class, ToStringSerializer.instance);
        simpleModule.addSerializer(Long.TYPE, ToStringSerializer.instance);
        mapper.registerModule(simpleModule);

        converter.setObjectMapper(mapper);
        return converter;
    }

    /**
     * i18n config - According to request headerAccept-LanguageSet locale
     */
    @Bean
    public LocaleResolver localeResolver() {
        return new AcceptHeaderLocaleResolver() {
            @Override
            public Locale resolveLocale(HttpServletRequest request) {
                String acceptLanguage = request.getHeader("Accept-Language");
                if (acceptLanguage == null || acceptLanguage.isEmpty()) {
                    return Locale.US;
                }

                // ParseAccept-LanguagePreferred language in request header
                String[] languages = acceptLanguage.split(",");
                if (languages.length > 0) {
                    // Extract first language code, remove possible quality value(q=...)
                    String[] parts = languages[0].split(";" + "\\s*");
                    String primaryLanguage = parts[0].trim();

                    // Create directly based on language code sent by frontendLocaleObject
                    if (primaryLanguage.equals("zh-CN")) {
                        return Locale.SIMPLIFIED_CHINESE;
                    } else if (primaryLanguage.equals("zh-TW")) {
                        return Locale.TRADITIONAL_CHINESE;
                    } else if (primaryLanguage.equals("en-US")) {
                        return Locale.US;
                    } else if (primaryLanguage.equals("de-DE")) {
                        return Locale.GERMANY;
                    } else if (primaryLanguage.equals("vi-VN")) {
                        return Locale.forLanguageTag("vi-VN");
                    } else if (primaryLanguage.startsWith("zh")) {
                        // For other Chinese variants, default use Simplified Chinese
                        return Locale.SIMPLIFIED_CHINESE;
                    } else if (primaryLanguage.startsWith("en")) {
                        // For other English variants, default use American English
                        return Locale.US;
                    } else if (primaryLanguage.startsWith("de")) {
                        // For other German variants, default use German
                        return Locale.GERMANY;
                    } else if (primaryLanguage.startsWith("vi")) {
                        // For other Vietnamese variants, default use Vietnamese
                        return Locale.forLanguageTag("vi-VN");
                    }
                }

                // If no matching language, use default language
                return Locale.US;
            }
        };
    }

}
