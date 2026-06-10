<template>
  <el-dialog :title="$t('nestLogin.title')" :visible.sync="visible" width="420px" @closed="reset" append-to-body>
    <template v-if="!mfaToken">
      <p class="muted">{{ $t('nestLogin.hint') }}</p>
      <el-form :model="form" label-width="90px" size="small">
        <el-form-item :label="$t('nestLogin.email')">
          <el-input v-model="form.email" autocomplete="username" />
        </el-form-item>
        <el-form-item :label="$t('nestLogin.password')">
          <el-input v-model="form.password" type="password" show-password autocomplete="current-password" @keyup.enter.native="submitLogin" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="visible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="loading" @click="submitLogin">{{ $t('nestLogin.signIn') }}</el-button>
      </span>
    </template>
    <template v-else>
      <p class="muted">{{ $t('nestLogin.mfaHint') }}</p>
      <el-input v-model="totp" :placeholder="$t('nestLogin.code')" size="small" maxlength="6" @keyup.enter.native="submitMfa" />
      <span slot="footer">
        <el-button size="small" @click="visible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="loading" @click="submitMfa">{{ $t('nestLogin.verify') }}</el-button>
      </span>
    </template>
  </el-dialog>
</template>

<script>
import Api from '@/apis/api';

export default {
  name: 'NestLoginDialog',
  data() {
    return {
      visible: false,
      loading: false,
      form: { email: '', password: '' },
      mfaToken: null,
      totp: '',
    };
  },
  methods: {
    open() {
      this.reset();
      this.visible = true;
    },
    reset() {
      this.form = { email: '', password: '' };
      this.mfaToken = null;
      this.totp = '';
      this.loading = false;
    },
    submitLogin() {
      if (!this.form.email || !this.form.password) {
        this.$message.warning(this.$t('nestLogin.required'));
        return;
      }
      this.loading = true;
      Api.nestAuth.login(
        this.form.email,
        this.form.password,
        (r) => {
          this.loading = false;
          if (r.done) this.done(r.role);
          else if (r.mfaRequired) this.mfaToken = r.mfaToken;
        },
        (msg) => {
          this.loading = false;
          this.$message.error(msg);
        },
      );
    },
    submitMfa() {
      if (!this.totp || this.totp.length !== 6) {
        this.$message.warning(this.$t('nestLogin.codeRequired'));
        return;
      }
      this.loading = true;
      Api.nestAuth.mfaVerify(
        this.mfaToken,
        this.totp,
        (r) => {
          this.loading = false;
          this.done(r.role);
        },
        (msg) => {
          this.loading = false;
          this.$message.error(msg);
        },
      );
    },
    done(role) {
      this.visible = false;
      this.$message.success(this.$t('nestLogin.ok', { role: role || 'admin' }));
      this.$emit('logged-in', role);
    },
  },
};
</script>

<style scoped>
.muted {
  color: #909399;
  margin-top: 0;
}
</style>
