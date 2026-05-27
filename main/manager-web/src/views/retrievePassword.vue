<template>
  <div class="welcome" @keyup.enter="retrievePassword">
    <el-container style="height: 100%;">
      <!-- Keep same header -->
      <el-header>
        <div style="display: flex;align-items: center;margin-top: 15px;margin-left: 10px;gap: 10px;">
          <img loading="lazy" alt="" src="@/assets/tbot-logo.png" style="width: 45px;height: 45px;" />
          <img loading="lazy" alt="" :src="tbotAiIcon" style="height: 18px;" />
        </div>
      </el-header>
      <div class="login-person">
        <img loading="lazy" alt="" src="@/assets/login/register-person.png" style="width: 100%;" />
      </div>
      <el-main style="position: relative;">
        <form @submit.prevent="retrievePassword">
          <div class="login-box">
            <!-- ModifyTitlePart -->
            <div style="display: flex;align-items: center;gap: 20px;margin-bottom: 39px;padding: 0 30px;">
              <img loading="lazy" alt="" src="@/assets/login/hi.png" style="width: 34px;height: 34px;" />
              <div class="login-text">{{ $t('retrievePassword.title') }}</div>
              <div class="login-welcome">
                {{ $t('retrievePassword.subtitle') }}
              </div>
            </div>

            <div style="padding: 0 30px;">
              <!-- Phone numberInput -->
              <div class="input-box">
                <div style="display: flex; align-items: center; width: 100%;">
                  <el-select v-model="form.areaCode" style="width: 220px; margin-right: 10px;">
                    <el-option v-for="item in mobileAreaList" :key="item.key" :label="`${item.name} (${item.key})`"
                      :value="item.key" />
                  </el-select>
                  <el-input v-model="form.mobile" :placeholder="$t('retrievePassword.mobilePlaceholder')" />
                </div>
              </div>

              <div style="display: flex; align-items: center; margin-top: 20px; width: 100%; gap: 10px;">
                <div class="input-box" style="width: calc(100% - 130px); margin-top: 0;">
                  <img loading="lazy" alt="" class="input-icon" src="@/assets/login/shield.png" />
                  <el-input v-model="form.captcha" :placeholder="$t('retrievePassword.captchaPlaceholder')" style="flex: 1;" />
                </div>
                <img loading="lazy" v-if="captchaUrl" :src="captchaUrl" alt="Verification code"
                  style="width: 150px; height: 40px; cursor: pointer;" @click="fetchCaptcha" />
              </div>

              <!-- Mobile verification code -->
              <div style="display: flex; align-items: center; margin-top: 20px; width: 100%; gap: 10px;">
                <div class="input-box" style="width: calc(100% - 130px); margin-top: 0;">
                  <img loading="lazy" alt="" class="input-icon" src="@/assets/login/phone.png" />
                  <el-input v-model="form.mobileCaptcha" :placeholder="$t('retrievePassword.mobileCaptchaPlaceholder')" style="flex: 1;" maxlength="6" />
                </div>
                <el-button type="primary" class="send-captcha-btn" :disabled="!canSendMobileCaptcha"
                  @click="sendMobileCaptcha">
                  <span>
                    {{ countdown > 0 ? `${countdown}${$t('register.secondsLater')}` : $t('retrievePassword.getMobileCaptcha') }}
                  </span>
                </el-button>
              </div>

              <!-- New password -->
              <div class="input-box">
                <img loading="lazy" alt="" class="input-icon" src="@/assets/login/password.png" />
                <el-input v-model="form.newPassword" :placeholder="$t('retrievePassword.newPasswordPlaceholder')" type="password" show-password />
              </div>

              <!-- ConfirmNew password -->
              <div class="input-box">
                <img loading="lazy" alt="" class="input-icon" src="@/assets/login/password.png" />
                <el-input v-model="form.confirmPassword" :placeholder="$t('retrievePassword.confirmNewPasswordPlaceholder')" type="password" show-password />
              </div>

              <!-- ModifyFooter link -->
              <div style="font-weight: 400;font-size: 14px;text-align: left;color: #5778ff;margin-top: 20px;">
                <div style="cursor: pointer;" @click="goToLogin">{{ $t('retrievePassword.goToLogin') }}</div>
              </div>
            </div>

            <!-- ModifyButton text -->
            <div class="login-btn" @click="retrievePassword">{{ $t('retrievePassword.resetButton') }}</div>

            <!-- Keep same protocol declaration -->
            <div style="font-size: 14px;color: #979db1;">
              {{ $t('retrievePassword.agreeTo') }}
              <div style="display: inline-block;color: #5778FF;cursor: pointer;" @click="openPage('/user-agreement.html')">{{ $t('register.userAgreement') }}</div>
              {{ $t('login.and') }}
              <div style="display: inline-block;color: #5778FF;cursor: pointer;" @click="openPage('/privacy-policy.html')">{{ $t('register.privacyPolicy') }}</div>
            </div>
          </div>
        </form>
      </el-main>

      <!-- Keep same footer -->
      <el-footer>
        <version-footer />
      </el-footer>
    </el-container>
  </div>
</template>

<script>
import Api from '@/apis/api';
import VersionFooter from '@/components/VersionFooter.vue';
import { getUUID, goToPage, showDanger, showSuccess, validateMobile, sm2Encrypt } from '@/utils';
import { mapState } from 'vuex';
import i18n from '@/i18n';

// ImportLanguageToggle function
import { changeLanguage } from '@/i18n';

export default {
  name: 'retrieve',
  components: {
    VersionFooter
  },
  computed: {
    ...mapState({
      allowUserRegister: state => state.pubConfig.allowUserRegister,
      mobileAreaList: state => state.pubConfig.mobileAreaList,
      sm2PublicKey: state => state.pubConfig.sm2PublicKey
    }),
    // Get currentLanguage
    currentLanguage() {
      return i18n.locale || "zh_CN";
    },
    // By currentLanguageGet correspondingtbot-aiIcon
    tbotAiIcon() {
      const currentLang = this.currentLanguage;
      switch (currentLang) {
        case "zh_CN":
          return require("@/assets/tbot-ai.png");
        case "zh_TW":
          return require("@/assets/tbot-ai_zh_TW.png");
        case "en":
          return require("@/assets/tbot-ai_en.png");
        case "de":
          return require("@/assets/tbot-ai_de.png");
        case "vi":
          return require("@/assets/tbot-ai_vi.png");
        case "pt_BR":
          return require("@/assets/tbot-ai_en.png");
        default:
          return require("@/assets/tbot-ai.png");
      }
    },
    canSendMobileCaptcha() {
      return this.countdown === 0 && validateMobile(this.form.mobile, this.form.areaCode);
    }
  },
  data() {
    return {
      form: {
        areaCode: '+86',
        mobile: '',
        captcha: '',
        captchaId: '',
        mobileCaptcha: '',
        newPassword: '',
        confirmPassword: ''
      },
      captchaUrl: '',
      countdown: 0,
      timer: null
    }
  },
  mounted() {
    this.fetchCaptcha();
  },
  methods: {
    openPage(url) {
      const lang = this.$i18n ? this.$i18n.locale : 'zh_CN';
      if (!lang.startsWith('zh')) {
        url = url.replace('.html', '-en.html');
      }
      window.open(url, '_blank');
    },
    // ReuseVerification codeGet method
    fetchCaptcha() {
      this.form.captchaId = getUUID();
      Api.user.getCaptcha(this.form.captchaId, (res) => {
        if (res.status === 200) {
          const blob = new Blob([res.data], { type: res.data.type });
          this.captchaUrl = URL.createObjectURL(blob);

        } else {
          console.error('Captcha load error:', error);
          showDanger(this.$t('register.captchaLoadFailed'));
        }
      });
    },

    // Wrap input validation logic
    validateInput(input, message) {
      if (!input.trim()) {
        showDanger(message);
        return false;
      }
      return true;
    },

    // SendMobile verification code
    sendMobileCaptcha() {
      if (!validateMobile(this.form.mobile, this.form.areaCode)) {
        showDanger(this.$t('retrievePassword.inputCorrectMobile'));
        return;
      }

      // VerifyGraphic CAPTCHA
      if (!this.validateInput(this.form.captcha, this.$t('retrievePassword.captchaRequired'))) {
        this.fetchCaptcha();
        return;
      }

      // Clear possible old timer
      if (this.timer) {
        clearInterval(this.timer);
        this.timer = null;
      }

      // Start countdown
      this.countdown = 60;
      this.timer = setInterval(() => {
        if (this.countdown > 0) {
          this.countdown--;
        } else {
          clearInterval(this.timer);
          this.timer = null;
        }
      }, 1000);

      // Call sendVerification codeInterface
      Api.user.sendSmsVerification({
        phone: this.form.areaCode + this.form.mobile,
        captcha: this.form.captcha,
        captchaId: this.form.captchaId
      }, (res) => {
        showSuccess(this.$t('retrievePassword.captchaSendSuccess'));
      }, (err) => {
        showDanger(err.data.msg || this.$t('register.captchaSendFailed'));
        this.countdown = 0;
        this.fetchCaptcha();
      });
    },

    // ModifyLogic
    retrievePassword() {
      // Validation logic
      if (!validateMobile(this.form.mobile, this.form.areaCode)) {
        showDanger(this.$t('retrievePassword.inputCorrectMobile'));
        return;
      }
      if (!this.form.captcha) {
        showDanger(this.$t('retrievePassword.captchaRequired'));
        return;
      }
      if (!this.form.mobileCaptcha) {
        showDanger(this.$t('retrievePassword.mobileCaptchaRequired'));
        return;
      }
      if (this.form.newPassword !== this.form.confirmPassword) {
        showDanger(this.$t('retrievePassword.passwordsNotMatch'));
        return;
      }

      // EncryptPassword
      let encryptedPassword;
      try {
        // ConcatGraphic CAPTCHAandNew passwordEncrypt
        const captchaAndPassword = this.form.captcha + this.form.newPassword;
        encryptedPassword = sm2Encrypt(this.sm2PublicKey, captchaAndPassword);
      } catch (error) {
        console.error("Password encryption failed:", error);
        showDanger(this.$t('sm2.encryptionFailed'));
        return;
      }

      Api.user.retrievePassword({
        phone: this.form.areaCode + this.form.mobile,
        password: encryptedPassword,
        code: this.form.mobileCaptcha,
        captchaId: this.form.captchaId
      }, (res) => {
        showSuccess(this.$t('retrievePassword.passwordUpdateSuccess'));
        goToPage('/login');
      }, (err) => {
        showDanger(err.data.msg || this.$t('message.error'));
        if (err.data != null && err.data.msg != null && (err.data.msg.indexOf('Graphic CAPTCHA') > -1 || err.data.msg.indexOf('Captcha') > -1)) {
          this.fetchCaptcha()
        }
      });
    },

    goToLogin() {
      goToPage('/login')
    }
  },
  beforeDestroy() {
    if (this.timer) {
      clearInterval(this.timer);
    }
  }
}
</script>

<style lang="scss" scoped>
@import './auth.scss';

.send-captcha-btn {
  margin-right: -5px;
  min-width: 100px;
  height: 40px;
  line-height: 40px;
  border-radius: 4px;
  font-size: 14px;
  background: rgb(87, 120, 255);
  border: none;
  padding: 0;

  &:disabled {
    background: #c0c4cc;
    cursor: not-allowed;
  }
}
</style>
