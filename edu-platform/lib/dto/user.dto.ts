export type UpdateUserBody = {
  real_name?: string;
  avatar_url?: string;
  email?: string;
  /** B3: persist QA logs when true. */
  qa_collection_enabled?: boolean;
  /** B3: set to true once to record that the student saw the collection notice. */
  qa_collection_notice_accepted?: boolean;
};
