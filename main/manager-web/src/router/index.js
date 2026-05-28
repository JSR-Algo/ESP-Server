import Vue from 'vue'
import VueRouter from 'vue-router'

Vue.use(VueRouter)

const routes = [
  {
    path: '/',
    name: 'welcome',
    component: function () {
      return import('../views/login.vue')
    }
  },
  {
    path: '/role-config',
    name: 'RoleConfig',
    component: function () {
      return import('../views/roleConfig.vue')
    },
    meta: { requiresAuth: true }
  },
  {
    path: '/voice-print',
    name: 'VoicePrint',
    component: function () {
      return import('../views/VoicePrint.vue')
    },
    meta: { requiresAuth: true }
  },
  {
    path: '/login',
    name: 'login',
    component: function () {
      return import('../views/login.vue')
    }
  },
  {
    path: '/home',
    name: 'home',
    component: function () {
      return import('../views/home.vue')
    },
    meta: { requiresAuth: true }
  },
  {
    path: '/register',
    name: 'Register',
    component: function () {
      return import('../views/register.vue')
    }
  },
  {
    path: '/retrieve-password',
    name: 'RetrievePassword',
    component: function () {
      return import('../views/retrievePassword.vue')
    }
  },
  // Device managementPage Route
  {
    path: '/device-management',
    name: 'DeviceManagement',
    component: function () {
      return import('../views/DeviceManagement.vue')
    },
    meta: { requiresAuth: true }
  },
  // AddUser managementRoute
  {
    path: '/user-management',
    name: 'UserManagement',
    component: function () {
      return import('../views/UserManagement.vue')
    },
    meta: { requiresAuth: true }
  },
  {
    path: '/model-config',
    name: 'ModelConfig',
    component: function () {
      return import('../views/ModelConfig.vue')
    },
    meta: { requiresAuth: true }
  },
  {
    path: '/params-management',
    name: 'ParamsManagement',
    component: function () {
      return import('../views/ParamsManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Parameter management'
    }
  },
  {
    path: '/knowledge-base-management',
    name: 'KnowledgeBaseManagement',
    component: function () {
      return import('../views/KnowledgeBaseManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Knowledge base management'
    }
  },
  {
    path: '/knowledge-file-upload',
    name: 'KnowledgeFileUpload',
    component: function () {
      return import('../views/KnowledgeFileUpload.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Document upload management'
    }
  },

  {
    path: '/server-side-management',
    name: 'ServerSideManager',
    component: function () {
      return import('../views/ServerSideManager.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Server management'
    }
  },
  {
    path: '/ota-management',
    name: 'OtaManagement',
    component: function () {
      return import('../views/OtaManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'OTA management'
    }
  },
  {
    path: '/voice-resource-management',
    name: 'VoiceResourceManagement',
    component: function () {
      return import('../views/VoiceResourceManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Voice resource activation'
    }
  },
  {
    path: '/voice-clone-management',
    name: 'VoiceCloneManagement',
    component: function () {
      return import('../views/VoiceCloneManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Voice clone management'
    }
  },
  {
    path: '/dict-management',
    name: 'DictManagement',
    component: function () {
      return import('../views/DictManagement.vue')
    },
    meta: { requiresAuth: true }
  },
  {
    path: '/provider-management',
    name: 'ProviderManagement',
    component: function () {
      return import('../views/ProviderManagement.vue')
    },
    meta: { requiresAuth: true }
  },
  // Add default role management route
  {
    path: '/agent-template-management',
    name: 'AgentTemplateManagement',
    component: function () {
      return import('../views/AgentTemplateManagement.vue')
    },
    meta: { requiresAuth: true }
  },
  // Add template quick config route
  {
    path: '/template-quick-config',
    name: 'TemplateQuickConfig',
    component: function () {
      return import('../views/TemplateQuickConfig.vue')
    },
    meta: { requiresAuth: true }
  },
  // Function configPage Route
  {
    path: '/feature-management',
    name: 'FeatureManagement',
    component: function () {
      return import('../views/FeatureManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Function config'
    }
  },
  // Replacement word management
  {
    path: '/replacement-word-management',
    name: 'ReplacementWordManagement',
    component: function () {
      return import('../views/ReplacementWordManagement.vue')
    },
    meta: {
      requiresAuth: true,
      title: 'Replacement word management'
    }
  },
]
const router = new VueRouter({
  base: process.env.VUE_APP_PUBLIC_PATH || '/',
  routes
})

const originalPush = VueRouter.prototype.push;
VueRouter.prototype.push = function push(location) {
  return originalPush.call(this, location).catch(err => {
    if (err.name === 'NavigationDuplicated') {
      return Promise.resolve();
    }
    throw err;
  });
};

router.beforeEach((to, from, next) => {
  if (to.meta && to.meta.requiresAuth) {
    const token = localStorage.getItem('token');
    if (!token) {
      next({ name: 'login', query: { redirect: to.fullPath } });
      return;
    }
  }
  next();
});

export default router
