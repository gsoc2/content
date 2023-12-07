#!/usr/bin/env bash
set +e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

get_user_info() {
  local user=$1
  local token=$2
  if [ -z "${user}" ] && [ -z "${token}" ]; then
    echo ""
  else
    # If either user or token is not empty, then we need to add them to the url.
    echo "${user}:${token}@"
  fi
}

clone_repository() {
  local host=$1
  local user=$2
  local token=$3
  local full_repo_path=$4
  local branch=$5
  local retry_count=$6
  local sleep_time=${7:-10}  # default sleep time is 10 seconds.
  local exit_code=0
  local i=1
  echo -e "${GREEN}Cloning ${full_repo_path} from ${host} branch:${branch} with ${retry_count} retries${NC}"

  user_info=$(get_user_info "${user}" "${token}")

  for ((i=1; i <= retry_count; i++)); do
    git clone --depth=1 "https://${user_info}${host}/${full_repo_path}.git" --branch "${branch}" && exit_code=0 && break || exit_code=$?
    if [ ${i} -ne "${retry_count}" ]; then
      echo -e "${YELLOW}Failed to clone ${full_repo_path} with branch:${branch}, exit code:${exit_code}, sleeping for ${sleep_time} seconds and trying again${NC}"
      sleep "${sleep_time}"
    else
      echo -e "${RED}Failed to clone ${full_repo_path} with branch:${branch}, exit code:${exit_code}, exhausted all ${retry_count} retries${NC}"
      break
    fi
  done
  return ${exit_code}
}

clone_repository_with_fallback_branch() {
  local host=$1
  local user=$2
  local token=$3
  local full_repo_path=$4
  local branch=$5
  local retry_count=$6
  local sleep_time=${7:-10}  # default sleep time is 10 seconds.
  local fallback_branch="${8:-master}"
  local repo_name=$9

  # Check if branch exists in the repository.
  echo -e "${GREEN}Checking if branch ${branch} exists in ${full_repo_path}${NC}"

  user_info=$(get_user_info "${user}" "${token}")

  git ls-remote --exit-code --quiet --heads "https://${user_info}${host}/${full_repo_path}.git" "refs/heads/${branch}" 1>/dev/null 2>&1
  local branch_exists=$?

  if [ "${branch_exists}" -ne 0 ]; then
    echo -e "${YELLOW}Branch ${branch} does not exist in ${full_repo_path}, defaulting to ${fallback_branch}${NC}"
    local exit_code=1
  else
    echo -e "${GREEN}Branch ${branch} exists in ${full_repo_path}, trying to clone${NC}"
    clone_repository "${host}" "${user}" "${token}" "${full_repo_path}" "${branch}" "${retry_count}" "${sleep_time}"
    local exit_code=$?
    if [ "${exit_code}" -ne 0 ]; then
      echo -e "${RED}Failed to clone ${full_repo_path} with branch:${branch}, exit code:${exit_code}${NC}"
    fi
  fi
  if [ "${exit_code}" -ne 0 ]; then
    # Trying to clone from fallback branch.
    echo -e "${YELLOW}Trying to clone repository:${full_repo_path} with fallback branch ${fallback_branch}!${NC}"
    clone_repository "${host}" "${user}" "${token}" "${full_repo_path}" "${fallback_branch}" "${retry_count}" "${sleep_time}"
    local exit_code=$?
    if [ ${exit_code} -ne 0 ]; then
      echo -e "${RED}ERROR: Failed to clone ${full_repo_path} with fallback branch:${fallback_branch}, exit code:${exit_code}, exiting!${NC}"
      exit ${exit_code}
    else
      echo "${fallback_branch}" > "${repo_name}".txt
      echo -e "${GREEN}Successfully cloned ${full_repo_path} with fallback branch:${fallback_branch}${NC}"
      return 0
    fi
  else
    echo "${branch}" > "${repo_name}".txt
    echo -e "${GREEN}Successfully cloned ${full_repo_path} with branch:${branch}${NC}"
    return 0
  fi
}

remove_cached_gitlab_repo() {
    local repo_name=$1
    echo "Removing $repo_name from the cache"
    rm -rf "./$repo_name"
}

clone_and_cache_gitlab_repos() {
  local host=$1
  local user=$2
  local token=$3
  local project_namespace=$4
  local branch=$5
  local retry_count=$6
  local sleep_time=${7:-10}  # default sleep time is 10 seconds.
  local fallback_branch="${8:-master}"
  local repo_name=$9
  local full_repo_path="${project_namespace}/${repo_name}"

  user_info=$(get_user_info "${user}" "${token}")

  if [ -f "${repo_name}.txt" ]; then
    cached_branch_name=$(cat "${repo_name}.txt")
    git ls-remote --exit-code --quiet --heads "https://${user_info}${host}/${full_repo_path}.git" "refs/heads/${branch}" 1>/dev/null 2>&1
    local branch_exists=$?
    if [ "${branch_exists}" -eq 0 ]; then
        if [ "${cached_branch_name}" != "${branch}" ]; then
          remove_cached_gitlab_repo "${repo_name}"
        fi
      else
        if [ "${cached_branch_name}" != "${fallback_branch}" ]; then
          remove_cached_gitlab_repo "${repo_name}"
        fi
      fi
  fi

  if [ -d "./${repo_name}" ] ; then
    echo "Fetching ${full_repo_path} repository with branch:${SEARCHED_BRANCH_NAME}"
    cd ./"${repo_name}"
    git remote set-url origin "https://${user_info}${host}/${full_repo_path}.git"
    git fetch -p -P
    cd ..
  else
    echo "Getting ${full_repo_path} repository with branch:${SEARCHED_BRANCH_NAME}, with fallback to master"
    clone_repository_with_fallback_branch "${host}" "${user}" "${token}" "${full_repo_path}" "${branch}" "${retry_count}" "${sleep_time}" "${fallback_branch}" "${repo_name}"
  fi

}

TEST_UPLOAD_BRANCH_SUFFIX="-upload_test_branch-"
# Search for the branch name without the suffix of '-upload_test_branch-' in case it exists.
if [[ "${CI_COMMIT_BRANCH}" == *"${TEST_UPLOAD_BRANCH_SUFFIX}"* ]]; then
  # Using bash string pattern matching to search only the last occurrence of the suffix, that's why we use a single '%'.
  SEARCHED_BRANCH_NAME="${CI_COMMIT_BRANCH%"${TEST_UPLOAD_BRANCH_SUFFIX}"*}"
  echo "Found branch with suffix ${TEST_UPLOAD_BRANCH_SUFFIX} in branch name, using the branch ${SEARCHED_BRANCH_NAME} to clone content-test-conf and infra repositories"
else
  # default to CI_COMMIT_BRANCH when the suffix is not found.
  echo "Didn't find a branch with suffix ${TEST_UPLOAD_BRANCH_SUFFIX} in branch name, using the branch ${CI_COMMIT_BRANCH} to clone content-test-conf and infra repositories, with fallback to master"
  SEARCHED_BRANCH_NAME="${CI_COMMIT_BRANCH}"
fi

CI_SERVER_HOST=${CI_SERVER_HOST:-code.pan.run}

echo "Getting content-test-conf and infra repositories with branch:${SEARCHED_BRANCH_NAME}"

clone_and_cache_gitlab_repos "${CI_SERVER_HOST}" "gitlab-ci-token" "${CI_JOB_TOKEN}" "${CI_PROJECT_NAMESPACE}" "${SEARCHED_BRANCH_NAME}" 3 10 "master" "content-test-conf"
clone_and_cache_gitlab_repos "${CI_SERVER_HOST}" "gitlab-ci-token" "${CI_JOB_TOKEN}" "${CI_PROJECT_NAMESPACE}" "${SEARCHED_BRANCH_NAME}" 3 10 "master" "infra"

set -e
echo "Successfully cloned content-test-conf and infra repositories"
