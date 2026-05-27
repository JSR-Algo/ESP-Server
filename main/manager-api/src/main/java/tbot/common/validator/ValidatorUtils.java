package tbot.common.validator;

import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

import org.hibernate.validator.messageinterpolation.ResourceBundleMessageInterpolator;
import org.springframework.context.i18n.LocaleContextHolder;
import org.springframework.context.support.ResourceBundleMessageSource;
import org.springframework.validation.beanvalidation.MessageSourceResourceBundleLocator;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import tbot.common.exception.RenException;

/**
 * hibernate-validatorValidation utility class
 * Reference document:http://docs.jboss.org/hibernate/validator/6.0/reference/en-US/html_single/
 */
public class ValidatorUtils {

    private static ResourceBundleMessageSource getMessageSource() {
        ResourceBundleMessageSource bundleMessageSource = new ResourceBundleMessageSource();
        bundleMessageSource.setDefaultEncoding("UTF-8");
        bundleMessageSource.setBasenames("i18n/validation");
        return bundleMessageSource;
    }

    /**
     * Validation Object
     *
     * @param object Object to validate
     * @param groups Group to validate
     */
    public static void validateEntity(Object object, Class<?>... groups)
            throws RenException {
        Locale.setDefault(LocaleContextHolder.getLocale());
        Validator validator = Validation.byDefaultProvider().configure().messageInterpolator(
                new ResourceBundleMessageInterpolator(new MessageSourceResourceBundleLocator(getMessageSource())))
                .buildValidatorFactory().getValidator();

        Set<ConstraintViolation<Object>> constraintViolations = validator.validate(object, groups);
        if (!constraintViolations.isEmpty()) {
            ConstraintViolation<Object> constraint = constraintViolations.iterator().next();
            throw new RenException(constraint.getMessage());
        }
    }

    /**
     * International phone number regex
     * Must include international calling code, format:+[Country Code][Phone number]
     * For example:
     * - +8613800138000
     * - +12345678900
     * - +447123456789
     */
    private static final String INTERNATIONAL_PHONE_REGEX = "^\\+[1-9]\\d{0,3}[1-9]\\d{4,14}$";

    /**
     * Validate phone number valid
     * Must include international calling code, format:+[Country Code][Phone number]
     * For example:+8613800138000
     * 
     * @param phone Phone number
     * @return boolean
     */
    public static boolean isValidPhone(String phone) {
        if (phone == null || phone.isEmpty()) {
            return false;
        }

        // Validate mobile number format must include international area code
        Pattern pattern = Pattern.compile(INTERNATIONAL_PHONE_REGEX);
        return pattern.matcher(phone).matches();
    }
}