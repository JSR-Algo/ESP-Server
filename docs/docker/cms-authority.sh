#!/usr/bin/env bash

configure_cms_authority() {
  : "${NESTJS_UPSTREAM_HOST:=tbot-backend-8wmh.onrender.com}"
  : "${NESTJS_UPSTREAM_SCHEME:=https}"
  : "${PUBLIC_CMS_UPSTREAM_HOST:=${NESTJS_UPSTREAM_HOST}}"
  : "${PUBLIC_CMS_UPSTREAM_SCHEME:=${NESTJS_UPSTREAM_SCHEME}}"
  : "${TBOT_ALLOW_SPLIT_CMS_AUTHORITY:=false}"

  case "${NESTJS_UPSTREAM_SCHEME}:${PUBLIC_CMS_UPSTREAM_SCHEME}" in
    http:http|http:https|https:http|https:https) ;;
    *)
      echo "cms-authority: upstream schemes must be http or https" >&2
      return 1
      ;;
  esac
  case "${TBOT_ALLOW_SPLIT_CMS_AUTHORITY}" in
    true|false) ;;
    *)
      echo "cms-authority: TBOT_ALLOW_SPLIT_CMS_AUTHORITY must be true or false" >&2
      return 1
      ;;
  esac

  if [[ "${NESTJS_UPSTREAM_SCHEME}://${NESTJS_UPSTREAM_HOST}" != \
        "${PUBLIC_CMS_UPSTREAM_SCHEME}://${PUBLIC_CMS_UPSTREAM_HOST}" && \
        "${TBOT_ALLOW_SPLIT_CMS_AUTHORITY}" != "true" ]]; then
    echo "cms-authority: CMS authority mismatch; set one authoritative host/scheme or explicitly enable the emergency override" >&2
    return 1
  fi
}
