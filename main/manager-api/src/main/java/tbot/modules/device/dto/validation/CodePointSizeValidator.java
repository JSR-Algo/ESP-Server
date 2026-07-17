package tbot.modules.device.dto.validation;

import jakarta.validation.ConstraintValidator;
import jakarta.validation.ConstraintValidatorContext;

public final class CodePointSizeValidator implements ConstraintValidator<CodePointSize, String> {
    private int maximum;

    @Override
    public void initialize(CodePointSize constraint) {
        maximum = constraint.max();
    }

    @Override
    public boolean isValid(String value, ConstraintValidatorContext context) {
        return value == null || value.codePointCount(0, value.length()) <= maximum;
    }
}
